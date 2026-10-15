from __future__ import annotations

import json
import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from .criteria import normalize_eligibility
from .funding import classify_funding, operator_group
from .parsers import ParsedOpportunity
from .contract import CONTRACT_VERSION, benefit_entries, condition_sections, prepare_record


DEFAULT_DATABASE_URL = (
    # The physical local database identity remains stable so the existing volume
    # can be reused. Product tables live in the `benefitlens` schema.
    "postgresql://unlock:unlock_local_dev@127.0.0.1:55432/unlock"
)
UUID_NAMESPACE = uuid.UUID("6cd07f21-11eb-4e76-9df1-0f738e1f96f0")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def connect() -> Connection[Any]:
    return psycopg.connect(database_url())


def migrate(migration_path: Path) -> None:
    paths = (
        sorted(migration_path.glob("*.sql"))
        if migration_path.is_dir()
        else [migration_path]
    )
    if not paths:
        raise ValueError(f"no migrations found: {migration_path}")
    with connect() as conn:
        for path in paths:
            conn.execute(path.read_text(encoding="utf-8"))


def stable_uuid(value: str) -> uuid.UUID:
    return uuid.uuid5(UUID_NAMESPACE, value)


def upsert_source(
    conn: Connection[Any],
    source: dict[str, Any],
) -> None:
    source_kind = str(source["source_kind"])
    approval_status = str(
        source.get(
            "approval_status",
            "not_applied" if source_kind == "official_api" else "not_required",
        )
    )
    conn.execute(
        """
        INSERT INTO platform.data_source (
            source_id, provider_name, provider_kind, source_group, source_kind,
            canonical_url, approval_status, rights_status,
            enabled, schedule_allowed, production_allowed, poll_interval_seconds,
            contract_version, updated_at
        ) VALUES (
            %(source_id)s, %(provider_name)s, %(provider_kind)s,
            %(source_group)s, %(source_kind)s, %(url)s, %(approval_status)s,
            %(rights_status)s, %(enabled)s, %(schedule_allowed)s,
            %(production_allowed)s, %(poll_interval_seconds)s,
            %(contract_version)s, now()
        )
        ON CONFLICT (source_id) DO UPDATE SET
            provider_name = EXCLUDED.provider_name,
            provider_kind = EXCLUDED.provider_kind,
            source_group = EXCLUDED.source_group,
            source_kind = EXCLUDED.source_kind,
            canonical_url = EXCLUDED.canonical_url,
            approval_status = EXCLUDED.approval_status,
            rights_status = EXCLUDED.rights_status,
            enabled = EXCLUDED.enabled,
            schedule_allowed = EXCLUDED.schedule_allowed,
            production_allowed = EXCLUDED.production_allowed,
            poll_interval_seconds = EXCLUDED.poll_interval_seconds,
            contract_version = EXCLUDED.contract_version,
            updated_at = now()
        """,
        {
            "source_id": source["source_id"],
            "provider_name": source["provider_name"],
            "provider_kind": source.get("provider_kind", "public_agency"),
            "source_group": source["source_group"],
            "source_kind": source_kind,
            "url": source["url"],
            "approval_status": approval_status,
            "rights_status": source.get("rights_status", "review_required"),
            "enabled": source.get("enabled", True),
            "schedule_allowed": source.get("schedule_allowed", False),
            "production_allowed": source.get("production_allowed", False),
            "poll_interval_seconds": source.get("poll_interval_seconds"),
            "contract_version": source.get("contract_version", "1"),
        },
    )


def start_run(
    conn: Connection[Any], source_id: str, *, parser_name: str | None
) -> tuple[uuid.UUID, datetime]:
    run_id = uuid.uuid4()
    started_at = utc_now()
    conn.execute(
        """
        INSERT INTO platform.collection_run (
            run_id, source_id, started_at, run_status, parser_name, parser_version
        ) VALUES (%s, %s, %s, 'running', %s, '1')
        """,
        (run_id, source_id, started_at, parser_name),
    )
    conn.commit()
    return run_id, started_at


def finish_run(
    conn: Connection[Any],
    run_id: uuid.UUID,
    *,
    status: str,
    http_status: int | None = None,
    request_url: str | None = None,
    final_url: str | None = None,
    content_type: str | None = None,
    raw_record_count: int | None = None,
    normalized_record_count: int | None = None,
    raw_payload_sha256: str | None = None,
    error: Exception | str | None = None,
) -> None:
    error_class: str | None = None
    error_message: str | None = None
    if error is not None:
        error_class = type(error).__name__ if isinstance(error, Exception) else "Blocked"
        error_message = str(error)[:2000]
    conn.execute(
        """
        UPDATE platform.collection_run
        SET finished_at = now(), run_status = %(status)s,
            http_status = %(http_status)s,
            request_url_redacted = %(request_url)s,
            final_url = %(final_url)s,
            content_type = %(content_type)s,
            raw_record_count = %(raw_record_count)s,
            normalized_record_count = %(normalized_record_count)s,
            raw_payload_sha256 = %(sha)s,
            error_class = %(error_class)s,
            error_message_redacted = %(error_message)s
        WHERE run_id = %(run_id)s
        """,
        {
            "run_id": run_id,
            "status": status,
            "http_status": http_status,
            "request_url": request_url,
            "final_url": final_url,
            "content_type": content_type,
            "raw_record_count": raw_record_count,
            "normalized_record_count": normalized_record_count,
            "sha": raw_payload_sha256,
            "error_class": error_class,
            "error_message": error_message,
        },
    )
    conn.commit()


def store_raw_document(
    conn: Connection[Any],
    *,
    run_id: uuid.UUID,
    source_id: str,
    source_url: str,
    final_url: str,
    fetched_at: datetime,
    http_status: int,
    content_type: str | None,
    headers: dict[str, str],
    body: bytes,
    body_sha256: str,
) -> int:
    row = conn.execute(
        """
        INSERT INTO platform.raw_document (
            run_id, source_id, source_url, final_url, fetched_at, http_status,
            content_type, response_headers, body, body_size_bytes, body_sha256
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, body_sha256) DO UPDATE
          SET body_sha256 = EXCLUDED.body_sha256
        RETURNING raw_document_id
        """,
        (
            run_id,
            source_id,
            source_url,
            final_url,
            fetched_at,
            http_status,
            content_type,
            Jsonb(headers),
            body,
            len(body),
            body_sha256,
        ),
    ).fetchone()
    assert row is not None
    return int(row[0])


def register_document_version(
    conn: Connection[Any],
    *,
    source_id: str,
    canonical_url: str,
    body_sha256: str,
    raw_document_id: int,
    seen_at: datetime,
) -> int:
    existing = conn.execute(
        """
        SELECT document_version_id
        FROM platform.document_version
        WHERE source_id = %s AND canonical_url = %s AND body_sha256 = %s
        """,
        (source_id, canonical_url, body_sha256),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE platform.document_version
            SET last_seen_at = %s
            WHERE document_version_id = %s
            """,
            (seen_at, existing[0]),
        )
        return int(existing[0])
    conn.execute(
        """
        UPDATE platform.document_version
        SET is_current = false, valid_to = %s
        WHERE source_id = %s AND canonical_url = %s AND is_current = true
        """,
        (seen_at, source_id, canonical_url),
    )
    row = conn.execute(
        """
        INSERT INTO platform.document_version (
            source_id, canonical_url, body_sha256, raw_document_id,
            first_seen_at, last_seen_at, is_current
        ) VALUES (%s, %s, %s, %s, %s, %s, true)
        RETURNING document_version_id
        """,
        (
            source_id,
            canonical_url,
            body_sha256,
            raw_document_id,
            seen_at,
            seen_at,
        ),
    ).fetchone()
    assert row is not None
    return int(row[0])


def record_quality(
    conn: Connection[Any],
    run_id: uuid.UUID,
    *,
    name: str,
    scope: str,
    status: str,
    message: str,
    observed: Any = None,
    expected: Any = None,
) -> None:
    conn.execute(
        """
        INSERT INTO platform.quality_result (
            run_id, check_name, check_scope, check_status, message,
            observed_value, expected_value
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, check_name, check_scope) DO UPDATE SET
            check_status = EXCLUDED.check_status,
            message = EXCLUDED.message,
            observed_value = EXCLUDED.observed_value,
            expected_value = EXCLUDED.expected_value
        """,
        (
            run_id,
            name,
            scope,
            status,
            message,
            Jsonb(observed),
            Jsonb(expected),
        ),
    )


def should_retire_missing(source: dict[str, Any]) -> bool:
    return source.get("complete_snapshot") is True


def _insert_extraction_candidates(
    conn: Connection[Any],
    *,
    opportunity_version_id: uuid.UUID,
    record: ParsedOpportunity,
    fallback_raw_document_id: int,
) -> None:
    for candidate in record.extraction_candidates:
        raw_document_id = candidate.get("raw_document_id")
        if not isinstance(raw_document_id, int) or raw_document_id <= 0:
            raw_document_id = fallback_raw_document_id
        proposed_value = {
            "text": str(candidate.get("proposed_value") or ""),
            "character_limit": candidate.get("character_limit"),
        }
        fingerprint_payload = {
            "field_kind": candidate.get("field_kind"),
            "proposed_value": proposed_value,
            "source_url": candidate.get("source_url"),
            "source_quote": candidate.get("source_quote"),
            "extractor_kind": candidate.get("extractor_kind"),
            "model_name": candidate.get("model_name"),
            "prompt_version": candidate.get("prompt_version"),
        }
        candidate_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")
        ).hexdigest()
        conn.execute(
            """
            INSERT INTO benefitlens.extraction_candidate (
                opportunity_version_id, raw_document_id, field_kind,
                proposed_value, source_url, source_kind, source_quote,
                confidence, extractor_kind, model_name, prompt_version,
                candidate_fingerprint_sha256
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (opportunity_version_id, candidate_fingerprint_sha256)
            DO NOTHING
            """,
            (
                opportunity_version_id,
                raw_document_id,
                candidate["field_kind"],
                Jsonb(proposed_value),
                candidate["source_url"],
                candidate["source_kind"],
                candidate["source_quote"],
                candidate["confidence"],
                candidate.get("extractor_kind") or "local_codex",
                candidate["model_name"],
                candidate["prompt_version"],
                candidate_fingerprint,
            ),
        )


def store_funding_classification(
    conn: Connection[Any], version_id: Any, title: str,
    benefits: Iterable[str], presentation: dict[str, Any] | None,
) -> dict[str, Any]:
    result = classify_funding(title, benefits, presentation)
    conn.execute(
        """
        INSERT INTO benefitlens.funding_classification (
            opportunity_version_id, classifier_version, tags_json,
            evidence_json, classification_status, reason
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (opportunity_version_id) DO UPDATE SET
            classifier_version = EXCLUDED.classifier_version,
            tags_json = EXCLUDED.tags_json, evidence_json = EXCLUDED.evidence_json,
            classification_status = EXCLUDED.classification_status,
            reason = EXCLUDED.reason, classified_at = now()
        """,
        (version_id, result["version"], Jsonb(result["tags"]),
         Jsonb(result["evidence"]), result["status"], result["reason"]),
    )
    return result


def classify_saved_opportunities() -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.opportunity_version_id, c.title, c.provider_kind,
                   v.presentation_json,
                   ARRAY(SELECT b.description FROM benefitlens.benefit b
                         WHERE b.opportunity_version_id = c.opportunity_version_id)
            FROM benefitlens.latest_candidate_opportunity c
            JOIN benefitlens.opportunity_version v USING (opportunity_version_id)
            """
        ).fetchall()
        tags: dict[str, int] = {}
        operators: dict[str, int] = {}
        targeted = 0
        for version_id, title, kind, presentation, benefits in rows:
            result = store_funding_classification(conn, version_id, title, benefits, presentation)
            if result["tags"]:
                targeted += 1
                group = operator_group(str(kind))
                operators[group] = operators.get(group, 0) + 1
            for tag in result["tags"]:
                tags[tag] = tags.get(tag, 0) + 1
    return {"registered": len(rows), "financial": targeted,
            "outside_or_unconfirmed": len(rows) - targeted,
            "funding_counts": tags, "operator_counts": operators}


def ingest_opportunities(
    conn: Connection[Any],
    *,
    source: dict[str, Any],
    run_id: uuid.UUID,
    document_version_id: int,
    raw_document_id: int,
    records: Iterable[ParsedOpportunity],
    seen_at: datetime,
    record_contexts: dict[str, tuple[int, int]] | None = None,
) -> int:
    count = 0
    seen_opportunity_ids: list[uuid.UUID] = []
    for record in records:
        record = prepare_record(record)
        normalized_eligibility = normalize_eligibility(record.eligibility_rules)
        content_fingerprint = hashlib.sha256(
            f"{record.fingerprint}:{normalized_eligibility.criteria_version}".encode()
        ).hexdigest()
        record_document_version_id, record_raw_document_id = (
            (record_contexts or {}).get(
                record.canonical_key,
                (document_version_id, raw_document_id),
            )
        )
        provider_name = record.provider_name or str(source["provider_name"])
        provider_kind = record.provider_kind or str(source["provider_kind"])
        provider_url = record.provider_url or str(source["url"])
        provider_id = stable_uuid(f"provider:{provider_name}")
        conn.execute(
            """
            INSERT INTO benefitlens.provider (
                provider_id, canonical_name, provider_kind, official_url
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (canonical_name) DO UPDATE SET
                provider_kind = EXCLUDED.provider_kind,
                official_url = EXCLUDED.official_url
            """,
            (provider_id, provider_name, provider_kind, provider_url),
        )
        opportunity_id = stable_uuid(f"opportunity:{record.canonical_key}")
        seen_opportunity_ids.append(opportunity_id)
        conn.execute(
            """
            INSERT INTO benefitlens.opportunity (
                opportunity_id, provider_id, canonical_key, first_seen_at
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT (canonical_key) DO UPDATE SET
                provider_id = EXCLUDED.provider_id,
                retired_at = NULL
            """,
            (opportunity_id, provider_id, record.canonical_key, seen_at),
        )
        existing = conn.execute(
            """
            SELECT opportunity_version_id
            FROM benefitlens.opportunity_version
            WHERE opportunity_id = %s AND content_fingerprint_sha256 = %s
              AND valid_to IS NULL
            """,
            (opportunity_id, content_fingerprint),
        ).fetchone()
        if existing:
            store_funding_classification(
                conn, existing[0], record.title,
                [record.benefit_description, *[b["description"] for b in record.additional_benefits]],
                record.presentation,
            )
            _insert_extraction_candidates(
                conn,
                opportunity_version_id=existing[0],
                record=record,
                fallback_raw_document_id=record_raw_document_id,
            )
            count += 1
            continue
        # A -> B -> A must append a new validity interval for A. Finding A in
        # history is not a no-op while B remains current. Identical current
        # content above still remains idempotent on repeated collection.
        version_id = stable_uuid(
            f"opportunity-version:{record.canonical_key}:{content_fingerprint}:{seen_at.isoformat()}"
        )
        conn.execute(
            """
            UPDATE benefitlens.opportunity_version
            SET valid_to = %s
            WHERE opportunity_id = %s AND valid_to IS NULL
            """,
            (seen_at, opportunity_id),
        )
        conn.execute(
            """
            INSERT INTO benefitlens.opportunity_version (
                opportunity_version_id, opportunity_id, run_id,
                document_version_id, content_fingerprint_sha256, title,
                summary, official_url, application_url, opens_at, closes_at,
                lifecycle_status, publication_status, criteria_version, valid_from,
                record_kind, presentation_json, evidence_sources_json, contract_version, condition_sections_json
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'needs_review', %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                version_id,
                opportunity_id,
                run_id,
                record_document_version_id,
                content_fingerprint,
                record.title,
                record.summary,
                record.official_url,
                record.application_url,
                record.opens_at,
                record.closes_at,
                record.lifecycle_status,
                normalized_eligibility.criteria_version,
                seen_at,
                record.record_kind,
                Jsonb(record.presentation),
                Jsonb(record.evidence_sources),
                CONTRACT_VERSION,
                Jsonb(condition_sections(record)),
            ),
        )
        benefits = benefit_entries(record)
        for benefit in benefits:
            conn.execute(
                """
                INSERT INTO benefitlens.benefit (
                    opportunity_version_id, benefit_type, description, amount_min, amount_max, currency, amount_unit, recurrence, amount_qualifier
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (version_id, benefit["benefit_type"], benefit["description"], benefit["amount_min"], benefit["amount_max"], benefit["currency"], benefit["currency"], benefit["recurrence"], benefit["amount_qualifier"]),
            )
        store_funding_classification(
            conn, version_id, record.title,
            [b["description"] for b in benefits], record.presentation,
        )
        group_ids: dict[str, uuid.UUID] = {}
        for group in normalized_eligibility.groups:
            group_id = stable_uuid(f"eligibility-group:{version_id}:{group.group_key}")
            group_ids[group.group_key] = group_id
            conn.execute(
                """
                INSERT INTO benefitlens.eligibility_rule_group (
                    rule_group_id, opportunity_version_id, group_key,
                    parent_rule_group_id, combinator, label, position
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    group_id,
                    version_id,
                    group.group_key,
                    group_ids.get(group.parent_group_key or ""),
                    group.combinator,
                    group.label,
                    group.position,
                ),
            )
        for rule in normalized_eligibility.rules:
            conn.execute(
                """
                INSERT INTO benefitlens.eligibility_rule (
                    opportunity_version_id, rule_type, operator, value_json,
                    rule_text, machine_evaluable, rule_key, rule_group_id,
                    criterion_code, profile_field, value_type, unit,
                    display_label, normalization_status, position, context_json
                ) VALUES (
                    %s, 'canonical', %s, %s, %s, true, %s, %s,
                    %s, %s, %s, %s, %s, 'normalized', %s, %s
                )
                """,
                (
                    version_id,
                    rule.operator,
                    Jsonb(rule.value),
                    rule.source_text,
                    rule.rule_key,
                    group_ids[rule.group_key],
                    rule.criterion_code,
                    rule.profile_field,
                    rule.value_type,
                    rule.unit,
                    rule.label,
                    rule.position,
                    Jsonb(rule.context),
                ),
            )
        for position, rule_text in enumerate(normalized_eligibility.unresolved_rules):
            conn.execute(
                """
                INSERT INTO benefitlens.eligibility_rule (
                    opportunity_version_id, rule_type, rule_text,
                    machine_evaluable, rule_key, rule_group_id,
                    normalization_status, position
                ) VALUES (
                    %s, 'unstructured', %s, false, %s, %s, 'unresolved', %s
                )
                """,
                (
                    version_id,
                    rule_text,
                    f"unresolved:{position + 1}",
                    group_ids["root"],
                    position,
                ),
            )
        for document_name in record.required_documents:
            conn.execute(
                """
                INSERT INTO benefitlens.required_document (
                    opportunity_version_id, document_name, requirement_text
                ) VALUES (%s, %s, %s)
                """,
                (version_id, document_name[:500], document_name),
            )
        for prompt in record.application_prompts:
            prompt_key = str(prompt.get("prompt_key") or "")
            prompt_text = str(prompt.get("prompt_text") or "")
            if not prompt_key or not prompt_text:
                continue
            conn.execute(
                """
                INSERT INTO benefitlens.application_prompt (
                    application_prompt_id, opportunity_version_id, prompt_key,
                    prompt_text, character_limit, required_elements, source_url
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    stable_uuid(f"application-prompt:{version_id}:{prompt_key}"),
                    version_id,
                    prompt_key,
                    prompt_text,
                    prompt.get("character_limit"),
                    Jsonb(prompt.get("required_elements") or []),
                    prompt.get("source_url") or record.official_url,
                ),
            )
        for field_name, evidence_text in record.evidence.items():
            if not evidence_text:
                continue
            conn.execute(
                """
                INSERT INTO benefitlens.evidence_span (
                    opportunity_version_id, raw_document_id, field_name, evidence_text
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    version_id,
                    record_raw_document_id,
                    field_name,
                    evidence_text[:4000],
                ),
            )
        _insert_extraction_candidates(
            conn,
            opportunity_version_id=version_id,
            record=record,
            fallback_raw_document_id=record_raw_document_id,
        )
        count += 1
    if seen_opportunity_ids and should_retire_missing(source):
        missing = conn.execute(
            """
            SELECT DISTINCT o.opportunity_id
            FROM benefitlens.opportunity o
            JOIN benefitlens.opportunity_version v USING (opportunity_id)
            JOIN platform.document_version d USING (document_version_id)
            WHERE d.source_id = %s
              AND v.valid_to IS NULL
              AND NOT (o.opportunity_id = ANY(%s))
            """,
            (source["source_id"], seen_opportunity_ids),
        ).fetchall()
        for row in missing:
            conn.execute(
                """
                UPDATE benefitlens.opportunity_version
                SET valid_to = %s, publication_status = 'rejected'
                WHERE opportunity_id = %s AND valid_to IS NULL
                """,
                (seen_at, row[0]),
            )
            conn.execute(
                "UPDATE benefitlens.opportunity SET retired_at = %s WHERE opportunity_id = %s",
                (seen_at, row[0]),
            )
    return count


def export_summary(conn: Connection[Any]) -> dict[str, Any]:
    history_counts: dict[str, int] = {}
    for label, table in (
        ("sources", "platform.data_source"),
        ("collection_runs", "platform.collection_run"),
        ("raw_documents", "platform.raw_document"),
        ("document_versions", "platform.document_version"),
        ("benefitlens_opportunities", "benefitlens.opportunity"),
        ("benefitlens_opportunity_versions", "benefitlens.opportunity_version"),
        ("benefitlens_benefits", "benefitlens.benefit"),
        ("benefitlens_eligibility_rules", "benefitlens.eligibility_rule"),
        ("benefitlens_eligibility_rule_groups", "benefitlens.eligibility_rule_group"),
        ("benefitlens_criteria_definitions", "benefitlens.criteria_definition"),
        ("benefitlens_profile_snapshots", "benefitlens.profile_snapshot"),
        ("benefitlens_evaluations", "benefitlens.evaluation"),
        ("benefitlens_required_documents", "benefitlens.required_document"),
        ("benefitlens_application_prompts", "benefitlens.application_prompt"),
        ("benefitlens_extraction_candidates", "benefitlens.extraction_candidate"),
        ("benefitlens_evidence_spans", "benefitlens.evidence_span"),
    ):
        row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
        history_counts[label] = int(row[0]) if row else 0
    current_counts: dict[str, int] = {}
    for label, query in (
        (
            "benefitlens_candidate_opportunities",
            "SELECT count(*) FROM benefitlens.latest_candidate_opportunity",
        ),
        (
            "benefitlens_candidate_benefit_rows",
            """
            SELECT count(*)
            FROM benefitlens.latest_candidate_opportunity c
            JOIN benefitlens.benefit b USING (opportunity_version_id)
            """,
        ),
        (
            "benefitlens_publishable_opportunities",
            "SELECT count(*) FROM benefitlens.latest_publishable_opportunity",
        ),
        (
            "benefitlens_current_structured_rules",
            """
            SELECT count(*)
            FROM benefitlens.latest_candidate_opportunity c
            JOIN benefitlens.eligibility_rule r USING (opportunity_version_id)
            JOIN benefitlens.criteria_definition d USING (criterion_code)
            WHERE r.machine_evaluable = true AND d.condition_kind = 'eligibility'
            """,
        ),
        (
            "benefitlens_current_participation_requirements",
            """
            SELECT count(*)
            FROM benefitlens.latest_candidate_opportunity c
            JOIN benefitlens.eligibility_rule r USING (opportunity_version_id)
            JOIN benefitlens.criteria_definition d USING (criterion_code)
            WHERE r.machine_evaluable = true AND d.condition_kind = 'requirement'
            """,
        ),
        (
            "benefitlens_current_unresolved_rules",
            """
            SELECT count(*)
            FROM benefitlens.latest_candidate_opportunity c
            JOIN benefitlens.eligibility_rule r USING (opportunity_version_id)
            WHERE r.machine_evaluable = false
              AND r.normalization_status = 'unresolved'
            """,
        ),
    ):
        row = conn.execute(query).fetchone()
        current_counts[label] = int(row[0]) if row else 0
    runs = conn.execute(
        """
        SELECT source_id, run_status, started_at, finished_at,
               raw_record_count, normalized_record_count, error_message_redacted
        FROM platform.collection_run
        ORDER BY started_at DESC, source_id
        """
    ).fetchall()
    opportunities = conn.execute(
        """
        SELECT provider_name, title, benefit_type, description,
               lifecycle_status, publication_status, official_url,
               x.structured_rule_count, x.unresolved_rule_count, x.match_mode
        FROM benefitlens.latest_candidate_opportunity c
        JOIN benefitlens.benefit b USING (opportunity_version_id)
        JOIN benefitlens.eligibility_coverage x USING (opportunity_version_id)
        ORDER BY provider_name, title
        """
    ).fetchall()
    latest_quality = conn.execute(
        """
        WITH latest_run AS (
            SELECT DISTINCT ON (source_id) run_id, source_id
            FROM platform.collection_run
            ORDER BY source_id, started_at DESC
        )
        SELECT l.source_id, q.check_name, q.check_status,
               q.observed_value, q.expected_value
        FROM latest_run l
        JOIN platform.quality_result q USING (run_id)
        ORDER BY l.source_id, q.check_name
        """
    ).fetchall()
    return {
        "generated_at": utc_now().isoformat(),
        "current_counts": current_counts,
        "history_counts": history_counts,
        "latest_quality": [
            {
                "source_id": row[0],
                "check": row[1],
                "status": row[2],
                "observed": row[3],
                "expected": row[4],
            }
            for row in latest_quality
        ],
        "runs": [
            {
                "source_id": row[0],
                "status": row[1],
                "started_at": row[2].isoformat(),
                "finished_at": row[3].isoformat() if row[3] else None,
                "raw_record_count": row[4],
                "normalized_record_count": row[5],
                "error": row[6],
            }
            for row in runs
        ],
        "benefitlens_candidate_benefit_rows": [
            {
                "provider": row[0],
                "title": row[1],
                "benefit_type": row[2],
                "benefit": row[3],
                "lifecycle_status": row[4],
                "publication_status": row[5],
                "official_url": row[6],
                "structured_rule_count": row[7],
                "unresolved_rule_count": row[8],
                "match_mode": row[9],
            }
            for row in opportunities
        ],
    }


def write_summary(path: Path) -> dict[str, Any]:
    with connect() as conn:
        result = export_summary(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return result
