from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_handoff import (COLUMNS, HandoffValidationError, review_mapping,
                                claim_scope_fingerprint, to_delivery_rows, validate_handoff)


def semantic(basis="agent_semantic_review"):
    return {"state": "accepted", "basis": basis, "reviewer": "fixture-agent",
            "decision_id": "fixture-decision", "reason": "Controlled test fixture, not a real source review",
            "review_receipt_ref": "fixture://review-receipt", "rule_id": "fixture-student-rule", "rule_version": "1"}


@pytest.fixture
def bundle(tmp_path):
    raw = b"<p>Students must remain enrolled.</p>"
    text = b"Students must remain enrolled.\nSubmit a research proposal.\n"
    (tmp_path / "raw.html").write_bytes(raw)
    (tmp_path / "text.txt").write_bytes(text)
    b = {
        "contract_version": "team-handoff-v1", "bundle_id": "fixture-bundle",
        "producer": "test", "created_at": "2026-11-09T00:00:00+00:00", "metadata": {"example_kind": "synthetic"},
        "programs": [{"program_id": "p", "title": "Fixture", "issuer": "Fixture University", "opportunity_type": "scholarship"}],
        "opportunity_rounds": [{"program_id": "p", "round_id": "r", "opportunity_id": "p-r", "notice_version": "v1",
            "status": "open", "official_url": "https://example.org/notice", "application_url": None,
            "opens_on": None, "closes_on": None, "announces_on": None, "checked_on": "2026-11-09"}],
        "source_documents": [{"document_id": "snapshot-1", "title": "Fixture", "url": "https://example.org/notice",
            "kind": "html", "capture_mode": "full_document", "fetched_at": "2026-11-09T00:00:00+00:00",
            "raw_sha256": sha256(raw).hexdigest(), "text_sha256": sha256(text).hexdigest(),
            "raw_path": "raw.html", "text_path": "text.txt", "access_scope": "public"}],
        "evidence": [{"evidence_id": "e", "document_id": "snapshot-1", "program_id": "p", "round_id": "r",
            "source_role": "official_notice", "locator": "L1", "quote": "Students must remain enrolled."}],
        "claims": [{"claim_id": "c", "program_id": "p", "round_id": "r", "kind": "eligibility",
            "field": "student_scope", "at": "participation", "value": {"student_scope_verified": True},
            "evidence_ids": ["e"], "semantic_review": semantic("existing_rule_review")}],
        "experience_cases": [], "case_documents": [], "writing_patterns": [], "pattern_evidence": [],
    }
    b["claims"][0]["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(b, b["claims"][0])
    return b


def codes(report):
    return {e["code"] for e in report["errors"]}


def add_pattern(b, monkeypatch):
    b["claims"][0].update(kind="submission", field="research_proposal", semantic_review=semantic())
    b["claims"][0]["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(b, b["claims"][0])
    b["writing_patterns"] = [{"pattern_id": "w", "program_id": "p", "target_round_id": "r", "target_claim_id": "c",
        "pattern_kind": "question", "body": {"question": "What is your verified research goal?"},
        "scope_status": "official_only", "causal_success_claim": False,
        "semantic_review": semantic() | {"input_scope_fingerprint": "controlled-scope"}}]
    b["pattern_evidence"] = [{"pattern_id": "w", "program_id": "p", "target_round_id": "r", "evidence_id": "e",
        "document_id": "snapshot-1", "source_program_id": "p", "source_round_id": "r",
        "case_id": None, "case_document_role": None, "purpose": "official_constraint"}]
    monkeypatch.setitem(sys.modules, "benefitlens_writing_handoff",
                        SimpleNamespace(writing_scope_fingerprint=lambda bundle, pattern: "controlled-scope"))


def test_roundtrip_uses_existing_columns_without_mutation(bundle, tmp_path):
    before = deepcopy(bundle)
    report = validate_handoff(bundle, tmp_path)
    assert report["valid"] and report["literal_checks"] == {"state": "verified", "documents": 1, "spans": 1}
    rows = to_delivery_rows(bundle, tmp_path)
    assert set(rows) == set(COLUMNS)
    assert len(rows["claim"]) == len(rows["claim_evidence"]) == 1
    assert set(rows["claim"][0]) == set(COLUMNS["claim"].split())
    assert rows["claim"][0]["review_state"] == "reviewed"
    assert rows["experience_case"] == rows["writing_pattern"] == []
    assert bundle == before


def test_publishing_projection_requires_literal_verification(bundle):
    assert validate_handoff(bundle)["literal_checks"]["state"] == "not_run"
    with pytest.raises(HandoffValidationError, match="literal_required"):
        to_delivery_rows(bundle)
    assert len(to_delivery_rows(bundle, structural_only=True)["claim"]) == 1


@pytest.mark.parametrize("field,change,code", [
    ("source_documents", {"text_sha256": "0" * 64}, "literal_artifact"),
    ("evidence", {"locator": "L2"}, "literal_quote"),
    ("source_documents", {"capture_mode": "public_web_excerpt"}, "excerpt_original"),
])
def test_literal_failures_are_not_semantic_approvals(bundle, tmp_path, field, change, code):
    bundle[field][0].update(change)
    assert code in codes(validate_handoff(bundle, tmp_path))
    with pytest.raises(HandoffValidationError):
        to_delivery_rows(bundle, tmp_path)


def test_same_company_family_does_not_allow_cross_variant_benefit(bundle):
    other = bundle["opportunity_rounds"][0] | {"round_id": "r2", "opportunity_id": "p-r2"}
    bundle["opportunity_rounds"].append(other)
    bundle["evidence"][0]["round_id"] = "r2"
    bundle["claims"][0].update(kind="benefit", field="support_components", value={"method": "grant"}, semantic_review=semantic())
    bundle["metadata"]["family_memberships"] = [
        {"program_id": "p", "round_id": r, "opportunity_family_id": "same-family", "rule_version": "1", "review_state": "candidate"}
        for r in ("r", "r2")]
    assert "evidence_scope" in codes(validate_handoff(bundle))


def test_same_snapshot_key_cannot_replace_bytes_but_exact_duplicates_collapse(bundle):
    bundle["source_documents"].append(deepcopy(bundle["source_documents"][0]))
    assert validate_handoff(bundle)["valid"]
    assert len(to_delivery_rows(bundle, structural_only=True)["source_document"]) == 1
    bundle["source_documents"][-1]["raw_sha256"] = "1" * 64
    assert "conflicting_identity" in codes(validate_handoff(bundle))


@pytest.mark.parametrize("value,source_status", [
    ({"status": "unknown", "satisfied": False}, None),
    ({"items": [{"state": "ambiguous", "eligible": False}]}, None),
    (False, "unknown"),
])
def test_unknown_cannot_become_ineligible(bundle, value, source_status):
    bundle["claims"][0]["value"] = value
    bundle["claims"][0]["source_status"] = source_status
    assert "unknown_became_false" in codes(validate_handoff(bundle))


def test_old_reviewed_flag_and_missing_provenance_remain_held(bundle):
    claim = bundle["claims"][0]
    claim.pop("semantic_review")
    claim["review_state"] = "reviewed"
    review = review_mapping(bundle)
    assert not review["accepted"] and review["held"][0]["record"] == claim
    assert to_delivery_rows(bundle, structural_only=True)["claim"] == []
    claim["semantic_review"] = semantic()
    claim["semantic_review"].pop("review_receipt_ref")
    assert "missing_review_receipt_ref" in review_mapping(bundle)["held"][0]["reasons"]


def test_candidate_without_evidence_is_preserved_and_never_fabricated(bundle):
    bundle["claims"][0].update(evidence_ids=[], semantic_review={"state": "candidate"}, value=None)
    assert validate_handoff(bundle)["valid"]
    assert review_mapping(bundle)["held"][0]["record"]["value"] is None
    rows = to_delivery_rows(bundle, structural_only=True)
    assert rows["claim"] == rows["claim_evidence"] == rows["experience_case"] == []


def test_machine_review_is_limited_to_existing_student_and_period_rules(bundle):
    bundle["claims"][0].update(kind="benefit", field="amount", value={"amount": 500})
    review = review_mapping(bundle)
    assert not review["accepted"] and "machine_scope_not_allowed" in review["rejected"][0]["reasons"]


@pytest.mark.parametrize("change", ["value", "quote", "round"])
def test_claim_review_cannot_be_reused_after_its_scoped_input_changes(bundle, change):
    if change == "value":
        bundle["claims"][0]["value"] = {"student_scope_verified": False}
    elif change == "quote":
        bundle["evidence"][0]["quote"] = "Different source wording"
    else:
        bundle["opportunity_rounds"][0]["round_id"] = "another-variant"
        bundle["claims"][0]["round_id"] = bundle["evidence"][0]["round_id"] = "another-variant"
    report = validate_handoff(bundle)
    assert report["valid"]
    review = review_mapping(bundle)
    assert not review["accepted"] and "scope_fingerprint_mismatch" in review["held"][0]["reasons"]
    assert not to_delivery_rows(bundle, structural_only=True)["claim"]


def test_claim_fingerprint_excludes_review_and_derived_writing_output(bundle):
    claim = bundle["claims"][0]
    expected = claim_scope_fingerprint(bundle, claim)
    claim["semantic_review"]["reason"] = "A revised receipt description"
    bundle["metadata"]["writing_result"] = {"missing_questions": ["A derived output"]}
    assert claim_scope_fingerprint(bundle, claim) == expected


def test_review_policy_changes_invalidate_claim_acceptance(bundle):
    for key, value in (("basis", "machine_rule"), ("rule_id", "another-rule"), ("rule_version", "2")):
        changed = deepcopy(bundle)
        changed["claims"][0]["semantic_review"][key] = value
        review = review_mapping(changed)
        assert not review["accepted"]
        assert "scope_fingerprint_mismatch" in review["held"][0]["reasons"]


def test_declared_dependencies_hold_claims_and_patterns_transitively(bundle, monkeypatch):
    add_pattern(bundle, monkeypatch)
    target = bundle["claims"][0]
    condition = deepcopy(target) | {"claim_id": "condition", "kind": "eligibility", "field": "student_scope",
                                  "semantic_review": semantic("existing_rule_review")}
    grant = deepcopy(target) | {"claim_id": "grant", "kind": "benefit", "field": "amount", "value": {"amount": 500},
                              "semantic_review": semantic()}
    bundle["claims"] += [grant, condition]
    bundle["metadata"]["claim_dependencies"] = {"c": ["grant"], "grant": ["condition"]}
    for claim in bundle["claims"]:
        claim["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(bundle, claim)
    condition["semantic_review"]["state"] = "candidate"
    review = review_mapping(bundle)
    assert not review["accepted"]
    held = {item["id"]: item for item in review["held"]}
    assert "dependency_not_accepted" in held["grant"]["reasons"]
    assert "dependency_not_accepted" in held["c"]["reasons"]
    assert "target_claim_not_accepted" in held["w"]["reasons"]
    projected = to_delivery_rows(bundle, structural_only=True)
    assert projected["claim"] == projected["writing_pattern"] == []
    assert held["grant"]["record"]["value"] == {"amount": 500}


def test_dependency_references_must_exist_in_the_same_recruitment_variant(bundle):
    for dependencies in ({"c": ["missing"]}, {"missing": ["c"]}):
        changed = deepcopy(bundle)
        changed["metadata"]["claim_dependencies"] = dependencies
        assert "dependency_missing_claim" in codes(validate_handoff(changed))
    other = deepcopy(bundle["claims"][0]) | {"claim_id": "other", "round_id": "another"}
    bundle["opportunity_rounds"].append(bundle["opportunity_rounds"][0] | {"round_id": "another", "opportunity_id": "another"})
    bundle["claims"].append(other)
    bundle["metadata"]["claim_dependencies"] = {"c": ["other"]}
    assert "dependency_scope" in codes(validate_handoff(bundle))


def test_dependency_cycles_are_rejected(bundle):
    bundle["claims"].append(deepcopy(bundle["claims"][0]) | {"claim_id": "other"})
    bundle["metadata"]["claim_dependencies"] = {"c": ["other"], "other": ["c"]}
    assert "claim_dependency_cycle" in codes(validate_handoff(bundle))
    with pytest.raises(HandoffValidationError, match="claim_dependency_cycle"):
        to_delivery_rows(bundle, structural_only=True)


def test_dependency_declaration_is_bound_but_no_dependencies_are_inferred(bundle):
    claim = bundle["claims"][0]
    claim.update(kind="benefit", field="conditional_amount", semantic_review=semantic(),
                 value={"amount": 500, "condition": {"status": "unknown"}})
    claim["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(bundle, claim)
    assert to_delivery_rows(bundle, structural_only=True)["claim"][0]["value"] == claim["value"]
    bundle["claims"].append(deepcopy(claim) | {"claim_id": "condition"})
    bundle["metadata"]["claim_dependencies"] = {"c": ["condition"]}
    held = {item["id"]: item for item in review_mapping(bundle)["held"]}
    assert "scope_fingerprint_mismatch" in held["c"]["reasons"]


def test_absent_inputs_do_not_create_experience_or_pattern_records(bundle, tmp_path):
    for key in ("programs", "opportunity_rounds", "source_documents", "evidence", "claims",
                "experience_cases", "case_documents", "writing_patterns", "pattern_evidence"):
        bundle[key] = []
    assert all(not rows for rows in to_delivery_rows(bundle, tmp_path).values())


def test_opportunity_form_is_not_a_payment_method(bundle):
    bundle["claims"][0].update(kind="benefit", field="components", value={"method": "internship"})
    assert "benefit_method_axis" in codes(validate_handoff(bundle))


def test_private_source_owner_and_access_declarations_are_enforced(bundle):
    doc = bundle["source_documents"][0]
    doc.pop("access_scope")
    assert not review_mapping(bundle)["accepted"]
    doc.update(access_scope="user_private", owner_subject_id="other", permission_basis="user_uploaded")
    bundle["metadata"]["subject_id"] = "applicant"
    assert "private_document_owner" in codes(validate_handoff(bundle))


def test_pattern_scope_fingerprint_and_target_acceptance_are_required(bundle, monkeypatch):
    add_pattern(bundle, monkeypatch)
    assert len(to_delivery_rows(bundle, structural_only=True)["writing_pattern"]) == 1
    bundle["writing_patterns"][0]["semantic_review"]["input_scope_fingerprint"] = "old"
    assert "scope_fingerprint_mismatch" in review_mapping(bundle)["held"][0]["reasons"]
    bundle["writing_patterns"][0]["semantic_review"]["input_scope_fingerprint"] = "controlled-scope"
    bundle["claims"][0]["semantic_review"]["state"] = "candidate"
    assert not to_delivery_rows(bundle, structural_only=True)["writing_pattern"]


def test_case_role_finality_and_binding_are_independent_checks(bundle, monkeypatch):
    add_pattern(bundle, monkeypatch)
    bundle["experience_cases"] = [{"case_id": "case", "program_id": "p", "experience_round_id": None,
        "experience_round_label": "submission round unknown", "outcome_status": "unknown", "outcome_basis": "unknown",
        "outcome_evidence_id": None, "notes": "Controlled historical fixture"}]
    bundle["case_documents"] = [{"case_id": "case", "document_id": "snapshot-1", "program_id": "p",
        "document_role": "application_statement", "finality": "unknown", "binding_evidence_id": None}]
    bundle["evidence"][0]["source_role"] = "application_statement"
    bundle["claims"][0].update(evidence_ids=[], semantic_review={"state": "candidate"})
    bundle["pattern_evidence"][0].update(purpose="case_observation", case_id="case", case_document_role="interview")
    assert "pattern_case_binding" in codes(validate_handoff(bundle))
    bundle["pattern_evidence"][0]["case_document_role"] = "application_statement"
    bundle["case_documents"][0]["finality"] = "confirmed_final"
    assert "unproven_finality" in codes(validate_handoff(bundle))


def test_known_field_validator_hook_and_json_schema(bundle):
    assert "field_validation" in codes(validate_handoff(bundle, field_validator=lambda claim: ["test typed rejection"]))
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = Path(__file__).resolve().parents[2] / "pipeline/schemas/team-handoff.schema.json"
    jsonschema.Draft202012Validator(json.loads(schema_path.read_text())).validate(bundle)


@pytest.mark.parametrize("change", [
    {"created_at": 5}, {"metadata": {"family_memberships": None}}, {"claims": [None]},
    {"evidence": [{"evidence_id": "e", "program_id": []}]},
])
def test_malformed_input_returns_a_report_instead_of_crashing(bundle, change):
    bundle.update(change)
    assert not validate_handoff(bundle)["valid"]
