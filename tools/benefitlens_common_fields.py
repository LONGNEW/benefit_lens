"""Typed, evidence-bound common fields used by the team handoff.

This validates representation, not the truth of an interpretation. Unregistered
legacy fields remain extensions; they are not silently normalized or approved.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from benefitlens_source_storage import read_source_bytes

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "pipeline/schemas/common-fields.schema.json"
VERSION = "common-field-v1"
COMMON_FIELDS = {
    ("eligibility", "enrollment_maintenance"),
    ("eligibility", "institution_scope"),
    ("eligibility", "concurrent_funding_rule"),
    ("submission", "application_route"),
    ("submission", "document_requirement"),
    ("benefit", "support_component"),
    ("activity", "funding_gap_rule"),
}


@lru_cache(maxsize=1)
def _validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_common_claim(claim):
    """Return representation errors for registered fields; leave extensions intact."""
    if (claim.get("kind"), claim.get("field")) not in COMMON_FIELDS:
        return []
    errors = [f"{'.'.join(map(str, e.absolute_path))}: {e.message}"
              for e in _validator().iter_errors(claim)]
    if errors:
        return errors
    value = claim["value"]
    if claim["field"] == "enrollment_maintenance" and claim.get("at") != "activity_period":
        errors.append("enrollment maintenance must reference the activity_period milestone")
    if claim["field"] == "support_component":
        amount = value["amount"]
        kind = amount["kind"]
        if kind == "range" and amount["min"] > amount["max"]:
            errors.append("amount range has min greater than max")
        if kind == "percentage" and not 0 <= amount["value"] <= 100:
            errors.append("percentage must be between 0 and 100")
        if amount.get("currency_code") and not set(amount["currency_evidence_ids"]) <= set(claim.get("evidence_ids", [])):
            errors.append("currency evidence must belong to this claim")
        if amount.get("currency_code") and not amount["currency_evidence_ids"]:
            errors.append("a currency code needs explicit currency evidence")
        if kind == "percentage" and value["method"] != "fee_waiver":
            errors.append("this version supports percentages only for fee waivers")
    return errors


def _fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def apply_reviewed_enrichments(bundle, specifications, *, reviewer, review_receipt_ref):
    """Attach explicitly reviewed local mappings without merging recruitment variants.

    A specification names an existing program, round and immutable source
    snapshot, and an exact LF-line quote. No HTTP, LLM, inference, or DB write is
    performed. The caller must subsequently run the full handoff validator.
    """
    if not reviewer or not review_receipt_ref:
        raise ValueError("reviewer and review receipt reference are required")
    result = deepcopy(bundle)
    documents = {d["document_id"]: d for d in result["source_documents"]}
    rounds = {(r["program_id"], r["round_id"]) for r in result["opportunity_rounds"]}
    bindings = {(e["program_id"], e.get("round_id"), e["document_id"])
                for e in result["evidence"] if e.get("source_role") in {"official_notice", "official_guideline", "official_form", "application_portal"}}
    claims = {c["claim_id"]: c for c in result["claims"]}
    evidence = {e["evidence_id"]: e for e in result["evidence"]}
    previous_decisions = result.get("metadata", {}).get("common_field_review", {}).get("mappings", [])
    decisions = {row["claim_id"]: deepcopy(row) for row in previous_decisions}
    changed_ids = []
    for spec in specifications:
        scope = (spec["program_id"], spec["round_id"])
        doc_id = spec["document_id"]
        if scope not in rounds or doc_id not in documents or (*scope, doc_id) not in bindings:
            raise ValueError("enrichment needs an existing same-round official document binding")
        doc = documents[doc_id]
        text_path = Path(doc["text_path"])
        if not text_path.is_absolute():
            raise ValueError("enrichment text path must be absolute")
        raw = read_source_bytes(text_path, storage=doc.get('text_storage'))
        if hashlib.sha256(raw).hexdigest() != doc["text_sha256"]:
            raise ValueError("enrichment source snapshot changed")
        quote = spec["quote"]
        text = raw.decode("utf-8")
        if not isinstance(quote, str) or not quote.strip() or quote not in text:
            raise ValueError("enrichment quote is not an exact original span")
        start = text.index(quote)
        line_start = text[:start].count("\n") + 1
        line_end = line_start + quote.count("\n")
        locator = f"L{line_start}-L{line_end}"
        core = {k: spec[k] for k in ("program_id", "round_id", "kind", "field", "value")}
        key = _fingerprint({**core, "document_id": doc_id, "quote": quote, "at": spec.get("at")})[:24]
        evidence_id, claim_id = "common-evidence-" + key, "common-claim-" + key
        decision_id = "common-review-" + _fingerprint({"mapping": key, "reviewer": reviewer, "receipt": review_receipt_ref})[:24]
        span = {"evidence_id": evidence_id, "document_id": doc_id, "program_id": scope[0],
                "round_id": scope[1], "source_role": spec.get("source_role", "official_notice"),
                "quote": quote, "locator": locator}
        claim = {**core, "claim_id": claim_id, "at": spec.get("at"), "review_state": "reviewed",
                 "evidence_ids": [evidence_id],
                 "semantic_review": {"state": "accepted", "basis": "agent_semantic_review",
                                     "reviewer": reviewer, "decision_id": decision_id,
                                     "reason": spec["reason"], "review_receipt_ref": review_receipt_ref}}
        errors = validate_common_claim(claim)
        if errors:
            raise ValueError("invalid common field: " + "; ".join(errors))
        for collection, record, identity in ((claims, claim, claim_id), (evidence, span, evidence_id)):
            previous = deepcopy(collection.get(identity))
            if previous and "semantic_review" in previous:
                previous["semantic_review"].pop("input_scope_fingerprint", None)
            if previous is not None and previous != record:
                raise ValueError("conflicting immutable enrichment ID")
            collection[identity] = record
        decisions[claim_id] = {"claim_id": claim_id, "decision_id": decision_id, "source_text_sha256": doc["text_sha256"],
                          "document_id": doc_id, "quote": quote, "locator": locator,
                          "basis": "agent_semantic_review", "scope": "explicit local mapping, not automatic source truth"}
        changed_ids.append(claim_id)
    result["claims"] = list(claims.values())
    result["evidence"] = list(evidence.values())
    from benefitlens_handoff import claim_scope_fingerprint
    for claim_id in changed_ids:
        claims[claim_id]["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(result, claims[claim_id])
    result.setdefault("metadata", {})["common_field_review"] = {
        "version": VERSION, "reviewer": reviewer, "review_receipt_ref": review_receipt_ref,
        "mappings": list(decisions.values()), "scope": "only supplied reviewed specifications; no full-corpus coverage claim"}
    return result
