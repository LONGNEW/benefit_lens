from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from . import db
from .http import SafeHttpClient
from .parsers import (
    ParsedOpportunity,
    enrich,
    parse,
    parse_gov24_records,
    parse_ontong_records,
)


def load_config(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _retained_notice_records(conn, source: dict, records: list[ParsedOpportunity]) -> list[ParsedOpportunity]:
    """Refresh retained active/undated notices even after they leave the first list page."""
    if not source.get("detail_parser"):
        return records
    rows = conn.execute("""
        SELECT o.canonical_key, c.title, c.official_url,
               COALESCE((SELECT e.evidence_text FROM benefitlens.evidence_span e
                         WHERE e.opportunity_version_id=c.opportunity_version_id
                         AND e.field_name='published_at' LIMIT 1), '')
        FROM benefitlens.latest_candidate_opportunity c
        JOIN benefitlens.opportunity o USING(opportunity_id)
        WHERE c.source_id=%s AND c.lifecycle_status<>'closed'
          AND (c.closes_at IS NULL OR c.closes_at >= now())
        ORDER BY c.valid_from DESC LIMIT 10
    """, (source["source_id"],)).fetchall()
    from .parsers import _dated_list_record, _infer_benefit_type
    known = {r.canonical_key for r in records}
    room = max(0, int(source.get("detail_limit", 10)) - len(records))
    retained = [_dated_list_record(ParsedOpportunity(canonical_key=key, title=title, summary=title,
                official_url=url, benefit_type=_infer_benefit_type(title), benefit_description=title,
                evidence={"title": title, "published_at": published}))
                for key, title, url, published in rows if key not in known][:room]
    return records + retained


def _codex_extractor(
    config: dict[str, object], config_path: Path, enabled: bool
) -> Any | None:
    if not enabled:
        return None
    codex_config = dict(config.get("codex_extraction", {}))
    command_env = str(
        codex_config.get("command_env") or "BENEFITLENS_CODEX_COMMAND"
    )
    command = os.environ.get(command_env, "").strip() or str(
        codex_config.get("command") or "codex"
    )
    model_env = str(codex_config.get("model_env") or "BENEFITLENS_CODEX_MODEL")
    model = os.environ.get(model_env, "").strip() or str(
        codex_config.get("model") or ""
    )
    schema_path = Path(
        str(codex_config.get("schema_path") or "benefitlens_extraction.schema.json")
    )
    if not schema_path.is_absolute():
        schema_path = config_path.parent / schema_path
    from .codex_extraction import LocalCodexExtractionClient

    return LocalCodexExtractionClient(
        command=command,
        schema_path=schema_path,
        model=model,
        reasoning_effort=str(codex_config.get("reasoning_effort") or "low"),
        timeout_seconds=int(codex_config.get("timeout_seconds", 180)),
        max_input_chars_per_record=int(
            codex_config.get("max_input_chars_per_record", 12000)
        ),
        max_records_per_run=int(codex_config.get("max_records_per_run", 100)),
        max_records_per_batch=int(codex_config.get("max_records_per_batch", 20)),
        target_record_kinds=tuple(
            str(value)
            for value in codex_config.get("target_record_kinds", ["notice"])
        ),
        include_ocr_images=bool(codex_config.get("include_ocr_images", True)),
        max_ocr_images_per_batch=int(
            codex_config.get("max_ocr_images_per_batch", 4)
        ),
        prompt_version=str(
            codex_config.get("prompt_version")
            or "benefitlens-codex-extraction-1"
        ),
    )


def _apply_codex_extraction(
    conn: Any,
    run_id: Any,
    extractor: Any | None,
    records: list[ParsedOpportunity],
) -> tuple[list[ParsedOpportunity], dict[str, object] | None]:
    if extractor is None:
        return records, None

    def image_loader(raw_document_id: int) -> tuple[bytes, str] | None:
        row = conn.execute(
            "SELECT body, content_type FROM platform.raw_document WHERE raw_document_id=%s",
            (raw_document_id,),
        ).fetchone()
        if not row or not row[0]:
            return None
        return bytes(row[0]), str(row[1] or "application/octet-stream")

    records, stats = extractor.enrich(records, image_loader=image_loader)
    has_warning = bool(stats["errors"]) or int(stats["rejected_candidates"]) > 0
    db.record_quality(
        conn,
        run_id,
        name="codex_assisted_extraction",
        scope="normalized_candidate",
        status="warn" if has_warning else "pass",
        message="로컬 Codex 구조화 결과의 원문 인용 일치 여부를 검사했습니다",
        observed=stats,
        expected={"errors": [], "rejected_candidates": 0},
    )
    return records, stats


def collect(
    config_path: Path,
    source_ids: set[str] | None = None,
    *,
    codex_enabled: bool = False,
) -> list[dict[str, object]]:
    config = load_config(config_path)
    extractor = _codex_extractor(config, config_path, codex_enabled)
    configured_sources = list(config.get("sources", []))
    if source_ids is None and config.get("school_directory", {}).get("enabled", False):
        from .school import discover_school_sources
        configured_sources.extend(discover_school_sources(dict(config["http"]), config_path.parent.parent / "data/exports/school_coverage.json"))
    configured_api_sources = list(config.get("api_sources", []))
    sources = [source for source in configured_sources if source.get("enabled", True)]
    if source_ids is not None:
        available = {str(source["source_id"]) for source in [*sources, *configured_api_sources] if source.get("enabled", True)}
        if source_ids - available:
            raise ValueError("unknown or disabled sources: " + ", ".join(sorted(source_ids - available)))
        sources = [source for source in sources if source["source_id"] in source_ids]
    api_sources = [
        source for source in configured_api_sources if source.get("enabled", True) and (source_ids is None or source["source_id"] in source_ids)
    ]
    blocked_sources = list(config.get("blocked_api_sources", [])) if source_ids is None else []
    http_config = dict(config.get("http", {}))
    allowed_hosts = {
        urlsplit(str(source["url"])).hostname or ""
        for source in [
            *configured_sources,
            *configured_api_sources,
            *blocked_sources,
        ]
    }
    allowed_hosts.update(http_config.get("linked_allowed_hosts", []))
    client = SafeHttpClient(
        allowed_hosts=allowed_hosts,
        user_agent=str(http_config["user_agent"]),
        timeout_seconds=int(http_config["timeout_seconds"]),
        max_body_bytes=int(http_config["max_body_bytes"]),
    )
    results: list[dict[str, object]] = []
    with db.connect() as conn:
        for source in sources:
            source = dict(source)
            db.upsert_source(conn, source)
            conn.commit()
            run_id, started_at = db.start_run(
                conn, str(source["source_id"]), parser_name=str(source["parser"])
            )
            try:
                fetched = client.get(str(source["url"]))
                raw_document_id = db.store_raw_document(
                    conn,
                    run_id=run_id,
                    source_id=str(source["source_id"]),
                    source_url=fetched.source_url,
                    final_url=fetched.final_url,
                    fetched_at=started_at,
                    http_status=fetched.status_code,
                    content_type=fetched.content_type,
                    headers=fetched.headers,
                    body=fetched.body,
                    body_sha256=fetched.sha256,
                )
                document_version_id = db.register_document_version(
                    conn,
                    source_id=str(source["source_id"]),
                    canonical_url=str(source["url"]),
                    body_sha256=fetched.sha256,
                    raw_document_id=raw_document_id,
                    seen_at=started_at,
                )
                records = parse(
                    str(source["parser"]), fetched.body, fetched.final_url
                )
                records = _retained_notice_records(conn, source, records)
                if not source.get("detail_parser"):
                    from .parsers import _soup, _text
                    text = _text(_soup(fetched.body).get_text(" ", strip=True)) if not fetched.body.startswith(b"%PDF") else ""
                    records = [replace(record, evidence_sources=record.evidence_sources or [{"url": fetched.final_url, "kind": "html", "status": "collected", "label": "공식 지원제도 본문", "text": text, "sha256": fetched.sha256}]) for record in records]
                if source.get("scope_department"):
                    scope = ["소속 학교: POSTECH", f"소속 학과: {source['scope_department']} [{source['scope_code']}]"]
                    records = [replace(record, eligibility_rules=scope + record.eligibility_rules, presentation={**record.presentation, "eligibility": scope + record.presentation.get("eligibility", [])}) for record in records]
                (
                    records,
                    record_contexts,
                    detail_raw_count,
                    detail_stats,
                ) = _collect_html_details(
                    conn,
                    client,
                    source,
                    records,
                    run_id,
                    started_at,
                )
                records, codex_stats = _apply_codex_extraction(
                    conn, run_id, extractor, records
                )
                keys = [record.canonical_key for record in records]
                duplicates = sorted({key for key in keys if keys.count(key) > 1})
                db.record_quality(
                    conn,
                    run_id,
                    name="http_success",
                    scope="raw",
                    status="pass",
                    message="공식 원천 응답을 저장했습니다",
                    observed={"status": fetched.status_code, "bytes": len(fetched.body)},
                    expected={"status": 200, "min_bytes": 1},
                )
                db.record_quality(
                    conn,
                    run_id,
                    name="normalized_count",
                    scope="normalized",
                    status="pass" if records else "fail",
                    message="정규화된 지원 항목 수를 검사했습니다",
                    observed=len(records),
                    expected=">= 1",
                )
                db.record_quality(
                    conn,
                    run_id,
                    name="duplicate_canonical_key",
                    scope="normalized",
                    status="fail" if duplicates else "pass",
                    message="한 실행 안의 지원 항목 키 중복을 검사했습니다",
                    observed=duplicates,
                    expected=[],
                )
                db.record_quality(
                    conn,
                    run_id,
                    name="detail_fetch_coverage",
                    scope="raw",
                    status=(
                        "pass"
                        if detail_stats["failed"] == 0
                        else "warn"
                    ),
                    message="목록에서 발견한 상세 공고 수집률을 검사했습니다",
                    observed=detail_stats,
                    expected={"failed": 0},
                )
                field_coverage = _field_coverage(records)
                linked_failures = [{"opportunity": record.canonical_key, "url": item.get("url"), "error": item.get("error")}
                                   for record in records for item in record.evidence_sources if item.get("status") == "failed"]
                db.record_quality(conn, run_id, name="linked_evidence_coverage", scope="raw",
                                  status="warn" if linked_failures else "pass",
                                  message="연결된 공식 페이지와 첨부자료 수집 실패를 별도로 기록했습니다",
                                  observed=linked_failures, expected=[])
                db.record_quality(
                    conn,
                    run_id,
                    name="normalized_field_coverage",
                    scope="normalized",
                    status=(
                        "pass"
                        if field_coverage["eligibility"] >= 0.5
                        else "warn"
                    ),
                    message="원문 자격 문장과 기간, 근거의 존재율입니다. 조건 해석 정확도와 구분합니다",
                    observed=field_coverage,
                    expected={"eligibility": ">= 0.5", "evidence": 1.0},
                )
                semantic_coverage = _normalization_coverage(records)
                db.record_quality(
                    conn, run_id, name="condition_normalization_coverage", scope="normalized",
                    status="pass" if semantic_coverage["structured_records"] == len(records) and records else "warn",
                    message="기준 규칙 변환과 미해석 조건을 검사합니다. 원문 완전성 승인은 별도입니다",
                    observed=semantic_coverage, expected={"unresolved_rules": 0, "review_required_records": 0},
                )
                if duplicates:
                    raise ValueError(f"duplicate canonical keys: {duplicates}")
                normalized_count = db.ingest_opportunities(
                    conn,
                    source=source,
                    run_id=run_id,
                    document_version_id=document_version_id,
                    raw_document_id=raw_document_id,
                    records=records,
                    seen_at=started_at,
                    record_contexts=record_contexts,
                )
                conn.commit()
                status = "quarantined"
                db.finish_run(
                    conn,
                    run_id,
                    status=status,
                    http_status=fetched.status_code,
                    request_url=fetched.source_url,
                    final_url=fetched.final_url,
                    content_type=fetched.content_type,
                    raw_record_count=1 + detail_raw_count,
                    normalized_record_count=normalized_count,
                    raw_payload_sha256=fetched.sha256,
                    error="공개 권리와 파싱 결과 사람 검토 전 격리",
                )
                results.append(
                    {
                        "source_id": source["source_id"],
                        "run_id": str(run_id),
                        "status": status,
                        "normalized_record_count": normalized_count,
                        "detail": detail_stats,
                        "codex_extraction": codex_stats,
                        "raw_sha256": fetched.sha256,
                    }
                )
            except Exception as exc:
                conn.rollback()
                db.finish_run(
                    conn,
                    run_id,
                    status="failed",
                    request_url=str(source["url"]),
                    error=exc,
                )
                results.append(
                    {
                        "source_id": source["source_id"],
                        "run_id": str(run_id),
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        for source in api_sources:
            source = dict(source)
            required_env = str(source["required_env"])
            secret = os.environ.get(required_env, "").strip()
            if not secret:
                results.append(_record_blocked_api(conn, source))
                continue
            db.upsert_source(conn, source)
            conn.commit()
            run_id, started_at = db.start_run(
                conn, str(source["source_id"]), parser_name=str(source["parser"])
            )
            try:
                if source["parser"] == "gov24_benefits_api":
                    records, aggregate, raw_ids, page_count = _collect_gov24(
                        conn,
                        client,
                        source,
                        secret,
                        run_id,
                        started_at,
                    )
                elif source["parser"] == "ontong_youth_policy_api":
                    records, aggregate, raw_ids, page_count = _collect_ontong(
                        conn,
                        client,
                        source,
                        secret,
                        run_id,
                        started_at,
                    )
                else:
                    raise ValueError(f"unknown API parser: {source['parser']}")
                records, codex_stats = _apply_codex_extraction(
                    conn, run_id, extractor, records
                )
                aggregate_body = json.dumps(
                    aggregate, ensure_ascii=False, sort_keys=True
                ).encode("utf-8")
                aggregate_sha = hashlib.sha256(aggregate_body).hexdigest()
                aggregate_raw_id = db.store_raw_document(
                    conn,
                    run_id=run_id,
                    source_id=str(source["source_id"]),
                    source_url=str(source["url"]),
                    final_url=str(source["url"]),
                    fetched_at=started_at,
                    http_status=200,
                    content_type="application/json; charset=utf-8",
                    headers={"x-local-artifact-kind": "aggregate-manifest"},
                    body=aggregate_body,
                    body_sha256=aggregate_sha,
                )
                document_version_id = db.register_document_version(
                    conn,
                    source_id=str(source["source_id"]),
                    canonical_url=str(source["url"]),
                    body_sha256=aggregate_sha,
                    raw_document_id=aggregate_raw_id,
                    seen_at=started_at,
                )
                keys = [record.canonical_key for record in records]
                duplicate_keys = sorted(
                    {key for key in keys if keys.count(key) > 1}
                )
                db.record_quality(
                    conn,
                    run_id,
                    name="api_pages_collected",
                    scope="raw",
                    status="pass",
                    message="API 전체 페이지와 합본을 저장했습니다",
                    observed={"pages": page_count, "page_raw_documents": len(raw_ids)},
                    expected={"minimum_pages": 1},
                )
                db.record_quality(
                    conn,
                    run_id,
                    name="duplicate_canonical_key",
                    scope="normalized",
                    status="fail" if duplicate_keys else "pass",
                    message="API 지원 항목의 고유 키를 검사했습니다",
                    observed=duplicate_keys,
                    expected=[],
                )
                if duplicate_keys:
                    raise ValueError(f"duplicate canonical keys: {duplicate_keys}")
                normalized_count = db.ingest_opportunities(
                    conn,
                    source=source,
                    run_id=run_id,
                    document_version_id=document_version_id,
                    raw_document_id=aggregate_raw_id,
                    records=records,
                    seen_at=started_at,
                )
                conn.commit()
                db.finish_run(
                    conn,
                    run_id,
                    status="quarantined",
                    http_status=200,
                    request_url=str(source["url"]),
                    final_url=str(source["url"]),
                    content_type="application/json",
                    raw_record_count=len(
                        aggregate.get("service_rows", aggregate.get("policy_rows", []))
                    ),
                    normalized_record_count=normalized_count,
                    raw_payload_sha256=aggregate_sha,
                    error="출처 표시와 정규화 결과 사람 검토 전 격리",
                )
                results.append(
                    {
                        "source_id": source["source_id"],
                        "run_id": str(run_id),
                        "status": "quarantined",
                        "normalized_record_count": normalized_count,
                        "api_page_count": page_count,
                        "codex_extraction": codex_stats,
                    }
                )
            except Exception as exc:
                conn.rollback()
                db.finish_run(
                    conn,
                    run_id,
                    status="failed",
                    request_url=str(source["url"]),
                    error=exc,
                )
                results.append(
                    {
                        "source_id": source["source_id"],
                        "run_id": str(run_id),
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        for source in blocked_sources:
            source = dict(source)
            results.append(_record_blocked_api(conn, source))
    return results


def _collect_html_details(
    conn: Any,
    client: SafeHttpClient,
    source: dict[str, Any],
    records: list[ParsedOpportunity],
    run_id: Any,
    started_at: Any,
) -> tuple[
    list[ParsedOpportunity],
    dict[str, tuple[int, int]],
    int,
    dict[str, object],
]:
    detail_parser = str(source.get("detail_parser") or "")
    if not detail_parser:
        return records, {}, 0, {"attempted": 0, "succeeded": 0, "failed": 0}
    detail_limit = min(int(source.get("detail_limit", len(records))), len(records))
    enriched: list[ParsedOpportunity] = []
    contexts: dict[str, tuple[int, int]] = {}
    errors: list[str] = []
    succeeded = 0
    raw_count = 0
    for index, record in enumerate(records):
        if index >= detail_limit:
            enriched.append(record)
            continue
        try:
            fetched = client.get(record.official_url)
            enriched_record = enrich(
                detail_parser,
                record,
                fetched.body,
                fetched.final_url,
            )
            raw_id = db.store_raw_document(
                conn,
                run_id=run_id,
                source_id=str(source["source_id"]),
                source_url=fetched.source_url,
                final_url=fetched.final_url,
                fetched_at=started_at,
                http_status=fetched.status_code,
                content_type=fetched.content_type,
                headers=fetched.headers,
                body=fetched.body,
                body_sha256=fetched.sha256,
            )
            raw_count += 1
            from .linked import enrich_linked
            def store_linked(item):
                nonlocal raw_count
                raw_count += 1
                return db.store_raw_document(conn, run_id=run_id, source_id=str(source["source_id"]), source_url=item.source_url, final_url=item.final_url, fetched_at=started_at, http_status=item.status_code, content_type=item.content_type, headers=item.headers, body=item.body, body_sha256=item.sha256)
            enriched_record = enrich_linked(enriched_record, fetched.body, fetched.final_url, client, store_linked)
            document_version_id = db.register_document_version(
                conn,
                source_id=str(source["source_id"]),
                canonical_url=record.official_url,
                body_sha256=fetched.sha256,
                raw_document_id=raw_id,
                seen_at=started_at,
            )
            contexts[record.canonical_key] = (document_version_id, raw_id)
            enriched.append(enriched_record)
            succeeded += 1
        except Exception as exc:
            # Do not replace a prior detailed version with a bare list row on failure.
            prior = conn.execute("SELECT 1 FROM benefitlens.opportunity o JOIN benefitlens.opportunity_version v USING(opportunity_id) WHERE o.canonical_key=%s AND v.valid_to IS NULL LIMIT 1", (record.canonical_key,)).fetchone()
            if not prior:
                enriched.append(record)
            errors.append(f"{record.canonical_key}: {type(exc).__name__}: {exc}")
    attempted = detail_limit
    return (
        enriched,
        contexts,
        raw_count,
        {
            "attempted": attempted,
            "succeeded": succeeded,
            "failed": attempted - succeeded,
            "errors": errors[:10],
        },
    )


def _field_coverage(records: list[ParsedOpportunity]) -> dict[str, float]:
    total = max(len(records), 1)
    return {
        "deadline": round(sum(bool(item.closes_at) for item in records) / total, 3),
        "eligibility": round(
            sum(bool(item.eligibility_rules) for item in records) / total,
            3,
        ),
        "evidence": round(sum(bool(item.evidence) for item in records) / total, 3),
    }


def _normalization_coverage(records: list[ParsedOpportunity]) -> dict[str, int]:
    from .contract import prepare_record
    from .criteria import normalize_eligibility
    values = [normalize_eligibility(prepare_record(record).eligibility_rules) for record in records]
    return {
        "records": len(values),
        "structured_records": sum(value.match_mode == "structured" for value in values),
        "partial_records": sum(value.match_mode == "partial" for value in values),
        "review_required_records": sum(value.match_mode == "review_required" for value in values),
        "structured_rules": sum(len(value.rules) for value in values),
        "unresolved_rules": sum(len(value.unresolved_rules) for value in values),
    }


def _record_blocked_api(
    conn: Any, source: dict[str, Any]
) -> dict[str, object]:
    source.setdefault("approval_status", "applied")
    source.setdefault("rights_status", "review_required")
    source["enabled"] = False
    db.upsert_source(conn, source)
    conn.commit()
    run_id, _ = db.start_run(conn, str(source["source_id"]), parser_name=None)
    reason = str(
        source.get("reason")
        or f"환경변수 {source.get('required_env')}가 필요함"
    )
    db.record_quality(
        conn,
        run_id,
        name="credential_available",
        scope="configuration",
        status="fail",
        message="승인된 API 키가 없어 실행을 차단했습니다",
        observed={"required_env": source.get("required_env")},
        expected="approved credential",
    )
    conn.commit()
    db.finish_run(conn, run_id, status="blocked", error=reason)
    return {
        "source_id": source["source_id"],
        "run_id": str(run_id),
        "status": "blocked",
        "error": reason,
    }


def _collect_gov24(
    conn: Any,
    client: SafeHttpClient,
    source: dict[str, Any],
    secret: str,
    run_id: Any,
    started_at: Any,
) -> tuple[list[ParsedOpportunity], dict[str, Any], list[int], int]:
    base_url = str(source["url"]).rsplit("/", 1)[0]
    decoded_secret = unquote(secret)
    secret_values = tuple(dict.fromkeys([secret, decoded_secret]))
    datasets = {
        "service_rows": f"{base_url}/serviceList",
        "detail_rows": f"{base_url}/serviceDetail",
        "condition_rows": f"{base_url}/supportConditions",
    }
    aggregate: dict[str, Any] = {}
    raw_ids: list[int] = []
    page_count = 0
    page_size = int(source.get("page_size", 1000))
    max_records = int(source.get("max_records", 100000))
    for dataset_name, endpoint in datasets.items():
        rows: list[dict[str, object]] = []
        page = 1
        total_count: int | None = None
        while total_count is None or len(rows) < total_count:
            fetched = client.get(
                endpoint,
                params={
                    "page": page,
                    "perPage": page_size,
                    "returnType": "JSON",
                    "serviceKey": decoded_secret,
                },
                secret_values=secret_values,
            )
            payload = json.loads(fetched.body.decode("utf-8-sig"))
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ValueError(f"Gov24 {dataset_name} response contract mismatch")
            page_rows = [item for item in payload["data"] if isinstance(item, dict)]
            total_count = int(payload.get("matchCount") or payload.get("totalCount") or 0)
            if total_count > max_records:
                raise ValueError(
                    f"Gov24 {dataset_name} totalCount={total_count} exceeds max_records={max_records}"
                )
            raw_ids.append(
                db.store_raw_document(
                    conn,
                    run_id=run_id,
                    source_id=str(source["source_id"]),
                    source_url=fetched.source_url,
                    final_url=fetched.final_url,
                    fetched_at=started_at,
                    http_status=fetched.status_code,
                    content_type=fetched.content_type,
                    headers=fetched.headers,
                    body=fetched.body,
                    body_sha256=fetched.sha256,
                )
            )
            page_count += 1
            rows.extend(page_rows)
            if not page_rows or len(rows) >= total_count:
                break
            page += 1
            if page > math.ceil(max(total_count, 1) / page_size) + 1:
                raise ValueError(f"Gov24 {dataset_name} pagination did not converge")
        aggregate[dataset_name] = rows
        db.record_quality(
            conn,
            run_id,
            name=f"{dataset_name}_count",
            scope="raw",
            status="pass" if len(rows) == total_count else "fail",
            message=f"Gov24 {dataset_name} 전체 건수를 검사했습니다",
            observed=len(rows),
            expected=total_count,
        )
    records = parse_gov24_records(
        aggregate["service_rows"],
        aggregate["detail_rows"],
        aggregate["condition_rows"],
    )
    return records, aggregate, raw_ids, page_count


def _collect_ontong(
    conn: Any,
    client: SafeHttpClient,
    source: dict[str, Any],
    secret: str,
    run_id: Any,
    started_at: Any,
) -> tuple[list[ParsedOpportunity], dict[str, Any], list[int], int]:
    endpoint = str(source["url"])
    rows: list[dict[str, object]] = []
    raw_ids: list[int] = []
    page_size = int(source.get("page_size", 100))
    max_records = int(source.get("max_records", 10000))
    page = 1
    total_count: int | None = None
    while total_count is None or len(rows) < total_count:
        fetched = client.get(
            endpoint,
            params={
                "apiKeyNm": secret,
                "pageNum": page,
                "pageSize": page_size,
                "pageType": 1,
                "rtnType": "json",
            },
            headers={"Accept": "application/json"},
            secret_values=(secret,),
        )
        payload = json.loads(fetched.body.decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Ontong response must be a JSON object")
        result_code = str(payload.get("resultCode") or "")
        result = payload.get("result")
        if result_code != "200" or not isinstance(result, dict):
            message = str(payload.get("resultMessage") or "response contract mismatch")
            raise ValueError(f"Ontong API error {result_code}: {message}")
        pagination = result.get("pagging")
        page_rows = result.get("youthPolicyList")
        if not isinstance(pagination, dict) or not isinstance(page_rows, list):
            raise ValueError("Ontong pagination or youthPolicyList is missing")
        reported_total = int(pagination.get("totCount") or 0)
        if total_count is None:
            total_count = reported_total
        elif reported_total != total_count:
            raise ValueError(
                f"Ontong total count changed during collection: {total_count} -> {reported_total}"
            )
        if total_count > max_records:
            raise ValueError(
                f"Ontong totalCount={total_count} exceeds max_records={max_records}"
            )
        normalized_page = [item for item in page_rows if isinstance(item, dict)]
        raw_ids.append(
            db.store_raw_document(
                conn,
                run_id=run_id,
                source_id=str(source["source_id"]),
                source_url=fetched.source_url,
                final_url=fetched.final_url,
                fetched_at=started_at,
                http_status=fetched.status_code,
                content_type=fetched.content_type,
                headers=fetched.headers,
                body=fetched.body,
                body_sha256=fetched.sha256,
            )
        )
        rows.extend(normalized_page)
        if not normalized_page or len(rows) >= total_count:
            break
        page += 1
        if page > math.ceil(max(total_count, 1) / page_size) + 1:
            raise ValueError("Ontong pagination did not converge")
    if len(rows) != total_count:
        raise ValueError(
            f"Ontong collected count={len(rows)} does not match totalCount={total_count}"
        )
    ids = [str(item.get("plcyNo") or "") for item in rows]
    duplicate_ids = sorted({item for item in ids if item and ids.count(item) > 1})
    db.record_quality(
        conn,
        run_id,
        name="policy_rows_count",
        scope="raw",
        status="pass",
        message="온통청년 API 전체 건수와 페이지 합계를 검사했습니다",
        observed=len(rows),
        expected=total_count,
    )
    db.record_quality(
        conn,
        run_id,
        name="policy_id_duplicates",
        scope="raw",
        status="fail" if duplicate_ids else "pass",
        message="온통청년 정책번호 중복을 검사했습니다",
        observed=duplicate_ids,
        expected=[],
    )
    if duplicate_ids:
        raise ValueError(f"Ontong duplicate policy IDs: {duplicate_ids}")
    aggregate = {
        "reported_total": total_count,
        "page_size": page_size,
        "policy_rows": rows,
    }
    return parse_ontong_records(rows), aggregate, raw_ids, len(raw_ids)
