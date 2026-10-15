from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from data_platform.db import connect
from data_platform.criteria import CRITERIA_VERSION
from data_platform.funding import operator_group


CATEGORY_LABELS = {
    "cash": "현금성",
    "education_cost": "교육비",
    "housing_finance": "주거와 금융",
    "goods": "물질성",
    "service": "서비스",
    "opportunity": "기회",
    "unknown": "기타",
}


def iso(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def chunks(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index:index + size]


def criteria_payload(
    connection: Any, opportunity_version_id: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    group_rows = connection.execute(
        """
        SELECT g.group_key, parent.group_key, g.combinator, g.label, g.position
        FROM benefitlens.eligibility_rule_group g
        LEFT JOIN benefitlens.eligibility_rule_group parent
          ON parent.rule_group_id = g.parent_rule_group_id
        WHERE g.opportunity_version_id = %s
        ORDER BY g.position
        """,
        (opportunity_version_id,),
    ).fetchall()
    rule_rows = connection.execute(
        """
        SELECT r.rule_key, g.group_key, r.criterion_code, r.profile_field,
               r.operator, r.value_json, r.value_type, r.unit,
               r.display_label, r.rule_text, r.position, d.condition_kind, r.context_json
        FROM benefitlens.eligibility_rule r
        JOIN benefitlens.eligibility_rule_group g USING (rule_group_id)
        JOIN benefitlens.criteria_definition d USING (criterion_code)
        WHERE r.opportunity_version_id = %s
          AND r.machine_evaluable = true
        ORDER BY r.position, r.eligibility_rule_id
        """,
        (opportunity_version_id,),
    ).fetchall()
    unresolved_rows = connection.execute(
        """
        SELECT rule_text
        FROM benefitlens.eligibility_rule
        WHERE opportunity_version_id = %s
          AND machine_evaluable = false
          AND normalization_status = 'unresolved'
        ORDER BY position, eligibility_rule_id
        """,
        (opportunity_version_id,),
    ).fetchall()

    groups: dict[str, dict[str, Any]] = {
        str(row[0]): {
            "kind": "group",
            "id": str(row[0]),
            "operator": str(row[2]),
            "label": str(row[3]),
            "children": [],
            "_parent": str(row[1]) if row[1] else None,
            "_position": int(row[4]),
        }
        for row in group_rows
    }
    for group in sorted(groups.values(), key=lambda item: item["_position"]):
        parent_key = group["_parent"]
        if parent_key and parent_key in groups:
            groups[parent_key]["children"].append(group)

    flat_rules: list[dict[str, Any]] = []
    for row in rule_rows:
        rule = {
            "kind": "rule",
            "id": str(row[0]),
            "criterionCode": str(row[2]),
            "field": str(row[3]),
            "operator": str(row[4]),
            "value": row[5],
            "valueType": str(row[6]),
            "unit": str(row[7]) if row[7] else None,
            "label": str(row[8] or row[9]),
            "sourceText": str(row[9]),
            "conditionKind": str(row[11]),
            "context": row[12] if len(row) > 12 and isinstance(row[12], dict) else {},
        }
        flat_rules.append(rule)
        group_key = str(row[1])
        if group_key in groups:
            groups[group_key]["children"].append(rule)

    for group in groups.values():
        group.pop("_parent", None)
        group.pop("_position", None)
    root = groups.get("root")
    unresolved = list(dict.fromkeys(str(row[0]) for row in unresolved_rows))
    return root, flat_rules, unresolved


def read_opportunities(mode: str = "approved") -> list[dict[str, Any]]:
    if mode not in {"candidate", "approved"}:
        raise ValueError("invalid publication mode")
    view = "latest_publishable_opportunity" if mode == "approved" else "latest_candidate_opportunity"
    query = """
        SELECT o.canonical_key,
               c.opportunity_version_id,
               c.provider_name,
               c.title,
               COALESCE(c.summary, ''),
               c.official_url,
               c.application_url,
               c.lifecycle_status,
               c.publication_status,
               c.source_id,
               c.source_group,
               COALESCE(c.last_collected_at, c.valid_from),
               c.opens_at,
               c.closes_at,
               v.criteria_version,
               COALESCE((
                   SELECT string_agg(b.description, ' / ' ORDER BY b.benefit_id)
                   FROM benefitlens.benefit b
                   WHERE b.opportunity_version_id = c.opportunity_version_id
               ), '지원 내용은 공식 원문에서 확인하세요.'),
               COALESCE((
                   SELECT b.benefit_type
                   FROM benefitlens.benefit b
                   WHERE b.opportunity_version_id = c.opportunity_version_id
                   ORDER BY b.benefit_id
                   LIMIT 1
               ), 'unknown'),
               c.provider_kind,
               v.record_kind,
               v.presentation_json,
               v.evidence_sources_json,
               COALESCE(f.tags_json, '[]'::jsonb),
               COALESCE(f.evidence_json, '[]'::jsonb),
               s.rights_status, s.production_allowed, v.condition_sections_json,
               COALESCE((SELECT jsonb_agg(jsonb_build_object(
                   'type', b.benefit_type, 'description', b.description,
                   'amountMin', b.amount_min, 'amountMax', b.amount_max,
                   'currency', b.currency, 'recurrence', b.recurrence,
                   'amountQualifier', b.amount_qualifier) ORDER BY b.benefit_id)
                   FROM benefitlens.benefit b WHERE b.opportunity_version_id = c.opportunity_version_id), '[]'::jsonb),
               COALESCE((SELECT jsonb_agg(d.document_name ORDER BY d.required_document_id)
                   FROM benefitlens.required_document d WHERE d.opportunity_version_id = c.opportunity_version_id), '[]'::jsonb),
               COALESCE((SELECT jsonb_agg(jsonb_build_object(
                   'id', p.application_prompt_id, 'opportunityVersionId', p.opportunity_version_id, 'promptKey', p.prompt_key, 'text', p.prompt_text,
                   'characterLimit', p.character_limit, 'sourceUrl', COALESCE(p.source_url, c.official_url),
                   'reviewStatus', c.publication_status) ORDER BY p.prompt_key)
                   FROM benefitlens.application_prompt p WHERE p.opportunity_version_id = c.opportunity_version_id), '[]'::jsonb),
               v.contract_version
        FROM benefitlens.latest_candidate_opportunity c
        JOIN benefitlens.opportunity o USING (opportunity_id)
        JOIN benefitlens.opportunity_version v USING (opportunity_version_id)
        LEFT JOIN benefitlens.funding_classification f USING (opportunity_version_id)
        JOIN platform.data_source s USING (source_id)
        ORDER BY c.provider_name, c.title, o.canonical_key
    """
    query = query.replace("benefitlens.latest_candidate_opportunity c", f"benefitlens.{view} c")
    with connect() as connection:
        records = connection.execute(query).fetchall()
        rows: list[dict[str, Any]] = []
        for record in records:
            criteria, rules, unresolved = criteria_payload(connection, str(record[1]))
            evidence_sources = record[20] or []
            for rule in rules:
                text = " ".join(rule["sourceText"].split())
                evidence = next((s for s in evidence_sources if text in " ".join(str(s.get("text", "")).split()) and s.get("status") in {"collected", "ocr_review"}), None)
                rule["sourceUrl"] = evidence["url"] if evidence else str(record[5])
                if rule["criterionCode"] == "remaining_study_months":
                    study = next((s["studyDuration"] for s in evidence_sources if s.get("status") == "collected" and s.get("studyDuration")), None)
                    if study:
                        rule["derivation"] = study
            benefit_type = str(record[16])
            provider_kind = str(record[17])
            group = operator_group(provider_kind)
            eligibility_notes = list(
                dict.fromkeys(
                    [str(rule["sourceText"]) for rule in rules if rule["conditionKind"] == "eligibility"] + unresolved
                )
            )
            match_mode = (
                "review_required"
                if not rules
                else "partial" if unresolved else "structured"
            )
            rows.append({
                "id": str(record[0]),
                "name": str(record[3]),
                "organization": str(record[2]),
                "support": str(record[15] or "혜택 항목 미구조화, 확보한 원문을 추가 해석해야 합니다."),
                "category": CATEGORY_LABELS.get(benefit_type, "기타"),
                "rulesJson": json.dumps(rules, ensure_ascii=False),
                "criteriaJson": json.dumps(criteria, ensure_ascii=False),
                "unresolvedCriteriaJson": json.dumps(unresolved, ensure_ascii=False),
                "caveat": "공식 본문과 연결 자료를 수집합니다. 개인 미입력, 자동 해석 미완료, 수집 실패는 구분해 표시합니다.",
                "sourceUrl": str(record[5]),
                "sourceUpdatedAt": iso(record[11]),
                "summary": str(record[4]),
                "applicationUrl": str(record[6]) if record[6] else None,
                "eligibilityJson": json.dumps(eligibility_notes, ensure_ascii=False),
                "benefitType": benefit_type,
                "lifecycleStatus": str(record[7]),
                "publicationStatus": str(record[8]),
                "sourceId": str(record[9]),
                "sourceGroup": str(record[10]),
                "operatorGroup": group,
                "matchMode": match_mode,
                "criteriaVersion": str(record[14]),
                "structuredRuleCount": sum(rule["conditionKind"] == "eligibility" for rule in rules),
                "unresolvedRuleCount": len(unresolved),
                "opensAt": iso(record[12]) or None,
                "closesAt": iso(record[13]) or None,
                "recordKind": str(record[18]),
                "presentationJson": json.dumps(record[19], ensure_ascii=False),
                "evidenceSourcesJson": json.dumps([{k:v for k,v in source.items() if k not in {"text", "rawDocumentId"}} for source in evidence_sources], ensure_ascii=False),
                "fundingTagsJson": json.dumps(record[21], ensure_ascii=False),
                "fundingEvidenceJson": json.dumps(record[22], ensure_ascii=False),
                "rightsStatus": str(record[23]),
                "productionAllowed": bool(record[24]),
                "conditionSectionsJson": json.dumps(record[25], ensure_ascii=False),
                "structuredBenefitsJson": json.dumps(record[26], ensure_ascii=False),
                "requiredDocumentsJson": json.dumps(record[27], ensure_ascii=False),
                "applicationPromptsJson": json.dumps(record[28], ensure_ascii=False),
                "contractVersion": str(record[29]),
                "opportunityVersionId": str(record[1]),
            })
    return rows


def post_json(base_url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = Request(
        f"{base_url.rstrip('/')}/api/admin/sync",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "benefitlens-d1-sync/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"D1 sync HTTP {error.code}: {detail[:500]}") from error
    except URLError as error:
        raise RuntimeError(f"D1 sync connection failed: {error.reason}") from error


def sync_resource(base_url: str, token: str, resource: str, rows: list[dict[str, Any]], chunk_size: int) -> None:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    dataset_version = f"postgres-{resource}-{generated_at}-{len(rows)}"
    post_json(base_url, token, {
        "mode": "start", "resource": resource, "datasetVersion": dataset_version,
        "expectedCount": len(rows),
    })
    loaded = 0
    for group in chunks(rows, chunk_size):
        response = post_json(base_url, token, {
            "mode": "chunk", "resource": resource, "datasetVersion": dataset_version,
            "rows": group,
        })
        loaded = int(response.get("loadedCount", loaded + len(group)))
        print(f"{resource}: {loaded}/{len(rows)}", file=sys.stderr)
    result = post_json(base_url, token, {
        "mode": "finish", "resource": resource, "datasetVersion": dataset_version,
    })
    print(json.dumps({"resource": resource, "loadedCount": result.get("loadedCount"), "status": result.get("status")}, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description="PostgreSQL 최신 후보를 웹 D1 read model로 복제합니다.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--chunk-size", type=int, default=40)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mode", choices=("approved", "candidate"), default="approved", help="승인 자료만 복제, 로컬 검토 데모는 candidate를 명시")
    args = parser.parse_args()
    if not 1 <= args.chunk_size <= 50:
        parser.error("--chunk-size must be between 1 and 50")

    datasets = {"opportunities": read_opportunities(args.mode)}
    print(json.dumps({resource: len(rows) for resource, rows in datasets.items()}, ensure_ascii=False))
    if args.dry_run:
        return 0

    token = os.environ.get("D1_SYNC_TOKEN")
    if not token:
        raise RuntimeError("D1_SYNC_TOKEN environment variable is required")
    for resource, rows in datasets.items():
        sync_resource(args.base_url, token, resource, rows, args.chunk_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
