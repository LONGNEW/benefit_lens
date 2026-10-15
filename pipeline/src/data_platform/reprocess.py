"""Replay saved official bytes, preserving original collection runs and history."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from . import db
from .benefitlens import load_config
from .linked import _primary_notice, merge_prompts
from .parsers import ParsedOpportunity, _enrich_notice, enrich, parse
from .reference_policies import apply_card_exclusions


def saved_school_scopes(config_path: Path, config: dict) -> dict[str, list[str]]:
    import json
    coverage_path = config_path.parent.parent / str(config.get("school_directory", {}).get("coverage_report", "data/exports/school_coverage.json"))
    if not coverage_path.exists():
        return {}
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    return {url: ["소속 학교: POSTECH", f"소속 학과: {department['department']} [{department['code']}]"] for department in coverage["departments"] for url in department.get("fundingPages", [])}


def text_notice(record: ParsedOpportunity, text: str, url: str) -> ParsedOpportunity:
    soup = BeautifulSoup("<main></main>", "html.parser")
    for line in text.splitlines():
        tag = soup.new_tag("p")
        tag.string = line
        soup.main.append(tag)
    return _enrich_notice(record, str(soup).encode(), url, selectors=("main",))


def reprocess_saved(config_path: Path) -> dict:
    config = load_config(config_path)
    configured = {source["source_id"]: source for source in config["sources"]}
    school_scopes = saved_school_scopes(config_path, config)
    seen_at = datetime.now(timezone.utc)
    with db.connect() as conn:
        cursor = conn.execute("""
            SELECT o.canonical_key, v.*, p.canonical_name AS provider_name,
                   p.provider_kind, p.official_url AS provider_url,
                   s.source_id, s.canonical_url AS source_url,
                   r.parser_name, d.raw_document_id, raw.body
            FROM benefitlens.latest_candidate_opportunity c
            JOIN benefitlens.opportunity o USING(opportunity_id)
            JOIN benefitlens.opportunity_version v USING(opportunity_version_id)
            JOIN benefitlens.provider p ON p.provider_id=o.provider_id
            JOIN platform.collection_run r ON r.run_id=v.run_id
            JOIN platform.data_source s ON s.source_id=r.source_id
            JOIN platform.document_version d USING(document_version_id)
            JOIN platform.raw_document raw ON raw.raw_document_id=d.raw_document_id
            ORDER BY o.canonical_key
        """)
        columns = [column.name for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        updated = []
        for row in rows:
            version = row["opportunity_version_id"]
            evidence = dict(conn.execute("SELECT field_name, evidence_text FROM benefitlens.evidence_span WHERE opportunity_version_id=%s", (version,)).fetchall())
            benefits = conn.execute("SELECT benefit_type, description FROM benefitlens.benefit WHERE opportunity_version_id=%s ORDER BY benefit_id", (version,)).fetchall()
            rules = [item[0] for item in conn.execute("SELECT DISTINCT rule_text FROM benefitlens.eligibility_rule WHERE opportunity_version_id=%s ORDER BY rule_text", (version,)).fetchall()]
            documents = [item[0] for item in conn.execute("SELECT document_name FROM benefitlens.required_document WHERE opportunity_version_id=%s ORDER BY required_document_id", (version,)).fetchall()]
            prompts = [{"prompt_key": item[0], "prompt_text": item[1], "character_limit": item[2], "required_elements": item[3], "source_url": item[4] or row["official_url"]} for item in conn.execute("SELECT prompt_key,prompt_text,character_limit,required_elements,source_url FROM benefitlens.application_prompt WHERE opportunity_version_id=%s ORDER BY prompt_key", (version,)).fetchall()]
            saved = ParsedOpportunity(
                canonical_key=row["canonical_key"], title=row["title"], summary=row["summary"] or "",
                official_url=row["official_url"], application_url=row["application_url"],
                opens_at=row["opens_at"].isoformat() if row["opens_at"] else None,
                closes_at=row["closes_at"].isoformat() if row["closes_at"] else None,
                lifecycle_status=row["lifecycle_status"], record_kind=row["record_kind"],
                provider_name=row["provider_name"], provider_kind=row["provider_kind"], provider_url=row["provider_url"],
                benefit_type=benefits[0][0] if benefits else "unknown", benefit_description=benefits[0][1] if benefits else "",
                additional_benefits=[{"benefit_type": item[0], "description": item[1]} for item in benefits[1:]],
                eligibility_rules=rules, required_documents=documents, application_prompts=prompts,
                presentation=row["presentation_json"], evidence_sources=row["evidence_sources_json"], evidence=evidence,
            )
            source = {**configured.get(row["source_id"], {}), "source_id": row["source_id"], "provider_name": row["provider_name"], "provider_kind": row["provider_kind"], "url": row["source_url"], "complete_snapshot": False}
            if saved.record_kind == "policy":
                parsed = parse(str(source.get("parser") or row["parser_name"]), bytes(row["body"]), row["source_url"])
                record = next((item for item in parsed if item.canonical_key == saved.canonical_key), None)
                if record is None:
                    raise ValueError("stored policy did not reproduce canonical key: " + saved.canonical_key)
                record = replace(record, provider_name=saved.provider_name, provider_kind=saved.provider_kind, provider_url=saved.provider_url, evidence_sources=saved.evidence_sources)
                scope = school_scopes.get(row["source_url"], []) if row["source_id"].startswith("postech-internal-") else []
                if scope:
                    record = replace(record, eligibility_rules=list(dict.fromkeys(scope + record.eligibility_rules)), presentation={**record.presentation, "eligibility": list(dict.fromkeys(scope + record.presentation.get("eligibility", [])))})
            elif any(item.get("kind") == "public_client_text" for item in saved.evidence_sources):
                # The exact public JS declaration needs its dedicated extractor.
                record = replace(saved, benefit_description=saved.evidence.get("benefit") or saved.benefit_description, additional_benefits=[])
            else:
                base = replace(saved, eligibility_rules=[], required_documents=[], application_prompts=[], presentation={}, additional_benefits=[], evidence_sources=[])
                parser_name = source.get("detail_parser")
                record = enrich(str(parser_name), base, bytes(row["body"]), saved.official_url) if parser_name else _enrich_notice(base, bytes(row["body"]), saved.official_url, selectors=(".board_view", ".nxb-view__contents", "#VIEW_MCONTENT", "main", "body"))
                for item in saved.evidence_sources:
                    if item.get("status") not in {"collected", "ocr_review"} or item.get("url") == saved.official_url:
                        continue
                    if item.get("kind") == "regulation":
                        record = apply_card_exclusions(record, str(item.get("text") or ""))
                        continue
                    if item.get("kind") not in {"html", "pdf", "image", "hwp", "hwpx"} or not item.get("text") or item.get("studyDuration") or "카드 신청 절차" in str(item.get("label")):
                        continue
                    raw = conn.execute("SELECT body FROM platform.raw_document WHERE raw_document_id=%s", (item.get("rawDocumentId"),)).fetchone() if item.get("rawDocumentId") else None
                    more, _ = _primary_notice(record, bytes(raw[0]), item["url"]) if item.get("kind") == "html" and raw else (text_notice(record, str(item["text"]), item["url"]), "")
                    primary = item["url"].startswith(("https://2026pre-skala.co.kr", "https://ncai.ndko.co.kr"))
                    presentation = more.presentation if primary else {key: list(dict.fromkeys(record.presentation.get(key, []) + more.presentation.get(key, []))) for key in {**record.presentation, **more.presentation}}
                    record = replace(record, eligibility_rules=more.eligibility_rules if primary else list(dict.fromkeys(record.eligibility_rules + more.eligibility_rules)),
                                     presentation=presentation, benefit_description=" / ".join(presentation.get("benefits", [])) or record.benefit_description,
                                     required_documents=list(dict.fromkeys(record.required_documents + more.required_documents)),
                                     application_prompts=merge_prompts(record.application_prompts, more.application_prompts, saved.official_url, item["url"]),
                                     evidence={**record.evidence, **more.evidence}, opens_at=more.opens_at or record.opens_at, closes_at=more.closes_at or record.closes_at, lifecycle_status=more.lifecycle_status)
                # Keep documented procedures that are not eligibility predicates.
                record = replace(record, presentation={**record.presentation, "conditions": list(dict.fromkeys(record.presentation.get("conditions", []) + [line for line in saved.presentation.get("conditions", []) if "고용24 구직 등록" in line or "고용센터의 지원 제외" in line or "훈련 진단과 상담" in line]))}, evidence_sources=saved.evidence_sources)
                if any(item.get("status") == "ocr_review" for item in saved.evidence_sources):
                    record = replace(record, eligibility_rules=record.eligibility_rules + ["이미지 OCR로 추출한 공고, 원본과 조건 대조 검토 필요"])
            db.ingest_opportunities(conn, source=source, run_id=row["run_id"], document_version_id=row["document_version_id"], raw_document_id=row["raw_document_id"], records=[record], seen_at=seen_at)
            updated.append(saved.canonical_key)
        return {"status": "reprocessed", "count": len(updated), "processed_at": seen_at.isoformat(), "collection_time_preserved": True, "network_requests": 0}
