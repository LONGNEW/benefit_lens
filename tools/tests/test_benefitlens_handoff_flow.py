from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_common_fields import apply_reviewed_enrichments, validate_common_claim
from benefitlens_handoff import HandoffValidationError, claim_scope_fingerprint
from run_benefitlens_handoff import (apply_review_patch, execute_handoff, fingerprint,
                                    import_delivery_packet, projection_audit)


def source_bundle(tmp_path):
    text = "Students remain enrolled during the internship.\nHourly range: $18.00 to $31.00.\n"
    path = tmp_path / "source.txt"
    path.write_text(text)
    checksum = sha256(path.read_bytes()).hexdigest()
    result = {"contract_version": "team-handoff-v1", "bundle_id": "synthetic-bundle",
              "producer": "test", "created_at": "2026-11-09T00:00:00+00:00",
              "programs": [{"program_id": "p", "title": "Fixture", "issuer": "Fixture University", "opportunity_type": "internship"}],
              "opportunity_rounds": [{"program_id": "p", "round_id": "r", "opportunity_id": "p-r", "notice_version": "v1", "status": "historical",
                  "official_url": "https://example.org/fixture", "application_url": None, "opens_on": None, "closes_on": None, "announces_on": None, "checked_on": "2026-11-09"}],
              "source_documents": [{"document_id": "snapshot", "title": "Synthetic fixture", "url": "https://example.org/fixture", "kind": "text", "capture_mode": "full_document",
                  "fetched_at": "2026-11-09T00:00:00+00:00", "raw_sha256": checksum, "text_sha256": checksum, "raw_path": str(path), "text_path": str(path), "access_scope": "public"}],
              "evidence": [{"evidence_id": "e", "document_id": "snapshot", "program_id": "p", "round_id": "r", "source_role": "official_notice", "locator": "L1",
                            "quote": "Students remain enrolled during the internship."}],
              "claims": [{"claim_id": "c", "program_id": "p", "round_id": "r", "kind": "eligibility", "field": "student_scope", "at": "observed",
                  "value": {"student_scope_verified": True}, "evidence_ids": ["e"], "semantic_review": {"state": "accepted", "basis": "existing_rule_review",
                      "reviewer": "synthetic-rule", "decision_id": "d", "reason": "Controlled fixture", "rule_id": "fixture", "rule_version": "1"}}],
              "experience_cases": [], "case_documents": [], "writing_patterns": [], "pattern_evidence": [], "metadata": {"example_kind": "synthetic"}}
    result["claims"][0]["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(result, result["claims"][0])
    return result


def wage_claim():
    return {"kind": "benefit", "field": "support_component", "evidence_ids": ["rate"], "value": {
        "schema_version": "common-field-v1", "component_id": "hourly-wage", "purpose": "unknown", "method": "wage", "beneficiary": "student", "conditions": [], "conditions_state": "not_reviewed",
        "amount": {"kind": "range", "value": None, "min": 18, "max": 31, "currency_code": None,
                   "raw_symbol": "$", "frequency": "per_hour", "currency_evidence_ids": [], "gross_or_net": "unknown"}}}


def test_range_and_unknown_currency_are_preserved():
    claim = wage_claim()
    assert validate_common_claim(claim) == []
    assert claim["value"]["amount"]["currency_code"] is None
    claim["value"]["amount"].update(min=40, max=31)
    assert any("min greater than max" in e for e in validate_common_claim(claim))


def test_currency_cannot_be_backfilled_from_dollar_symbol():
    claim = wage_claim()
    claim["value"]["amount"]["currency_code"] = "USD"
    assert any("explicit currency evidence" in e for e in validate_common_claim(claim))
    claim["value"]["amount"]["currency_evidence_ids"] = ["unrelated"]
    assert any("belong to this claim" in e for e in validate_common_claim(claim))


def test_fee_percentage_does_not_become_cash_or_guaranteed_total():
    claim = wage_claim()
    claim["value"].update(method="fee_waiver", purpose="tuition")
    claim["value"]["amount"].update(kind="percentage", value=100, min=None, max=None, frequency="unknown")
    assert not validate_common_claim(claim)
    claim["value"]["amount"]["value"] = 101
    assert validate_common_claim(claim)
    claim["value"]["amount"].update(value=100, min=100)
    assert validate_common_claim(claim)


def test_enrichment_is_same_round_bound_idempotent_and_non_destructive(tmp_path):
    bundle = source_bundle(tmp_path)
    original = deepcopy(bundle)
    spec = {"program_id": "p", "round_id": "r", "document_id": "snapshot", "kind": "eligibility", "field": "enrollment_maintenance",
            "at": "activity_period", "quote": "Students remain enrolled during the internship.", "reason": "Synthetic source mapping",
            "value": {"schema_version": "common-field-v1", "enrollment_status": "enrolled", "full_time": False, "throughout": True}}
    result = apply_reviewed_enrichments(bundle, [spec], reviewer="fixture-agent", review_receipt_ref="fixture-review")
    again = apply_reviewed_enrichments(result, [spec], reviewer="fixture-agent", review_receipt_ref="fixture-review")
    assert again == result
    assert bundle == original
    assert len(result["claims"]) == 2
    with pytest.raises(ValueError, match="same-round"):
        apply_reviewed_enrichments(bundle, [spec | {"round_id": "another"}], reviewer="fixture-agent", review_receipt_ref="fixture-review")
    with pytest.raises(ValueError, match="exact original"):
        apply_reviewed_enrichments(bundle, [spec | {"quote": "No enrollment is required."}], reviewer="fixture-agent", review_receipt_ref="fixture-review")


def test_flow_preserves_pending_candidates_and_replays_unchanged(tmp_path):
    bundle = source_bundle(tmp_path)
    held = deepcopy(bundle["claims"][0])
    held.update(claim_id="pending-benefit", kind="benefit", field="source_specific_allowance", value={"status": "unknown"},
                semantic_review={"state": "candidate", "reason": "No reviewed amount"})
    bundle["claims"].append(held)
    receipt = execute_handoff(bundle, tmp_path / "state")
    assert receipt["validation"]["valid"]
    assert receipt["projected_rows"]["claim"] == 1
    assert receipt["review"]["held"]["count"] == 1
    saved = json.loads(Path(receipt["artifacts"]["bundle"]["path"]).read_text())
    assert saved["claims"][1]["value"] == {"status": "unknown"}
    again = execute_handoff(bundle, tmp_path / "state")
    assert again["run_id"] == receipt["run_id"] and again["cache_hit"]
    assert again["external_model_calls"] == again["new_http_requests"] == 0


def test_stale_review_is_not_exported_after_value_change(tmp_path):
    bundle = source_bundle(tmp_path)
    bundle["claims"][0]["value"]["student_scope_verified"] = False
    result = execute_handoff(bundle, tmp_path / "state")
    assert result["projected_rows"]["claim"] == 0
    assert result["review"]["held"]["count"] == 1


def test_mutated_source_cannot_reuse_an_old_success(tmp_path):
    bundle = source_bundle(tmp_path)
    execute_handoff(bundle, tmp_path / "state")
    Path(bundle["source_documents"][0]["text_path"]).write_text("Changed source")
    with pytest.raises(HandoffValidationError):
        execute_handoff(bundle, tmp_path / "state")


def test_snapshot_id_cannot_be_reused_with_changed_valid_hash(tmp_path):
    bundle = source_bundle(tmp_path)
    execute_handoff(bundle, tmp_path / "state")
    path = Path(bundle["source_documents"][0]["text_path"])
    path.write_text(path.read_text() + "An extra line.\n")
    checksum = sha256(path.read_bytes()).hexdigest()
    bundle["source_documents"][0].update(raw_sha256=checksum, text_sha256=checksum)
    bundle["claims"][0]["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(bundle, bundle["claims"][0])
    with pytest.raises(ValueError, match="snapshot ID reused"):
        execute_handoff(bundle, tmp_path / "state")


def test_cached_output_tampering_is_detected(tmp_path):
    bundle = source_bundle(tmp_path)
    receipt = execute_handoff(bundle, tmp_path / "state")
    Path(receipt["artifacts"]["writing"]["path"]).write_text("{}")
    with pytest.raises(ValueError, match="output is missing or changed"):
        execute_handoff(bundle, tmp_path / "state")


def test_legacy_reviewed_flag_does_not_approve_import(tmp_path):
    from benefitlens_handoff import to_delivery_rows
    bundle = source_bundle(tmp_path)
    rows = to_delivery_rows(bundle, Path("/"))
    data = tmp_path / "legacy" / "data"
    data.mkdir(parents=True)
    (data / "rows.json").write_text(json.dumps(rows))
    imported = import_delivery_packet(data)
    receipt = execute_handoff(imported, tmp_path / "state")
    assert receipt["projected_rows"]["claim"] == 0
    assert receipt["review"]["held"]["count"] == 1


def test_review_patch_is_exact_base_bound_and_cannot_change_facts(tmp_path):
    bundle = source_bundle(tmp_path)
    patch = {"base_bundle_sha256": fingerprint(bundle), "reviews": {"c": bundle["claims"][0]["semantic_review"]},
             "review_receipt_ref": "fixture://new-review"}
    changed = apply_review_patch(bundle, patch)
    assert changed["claims"][0]["value"] == bundle["claims"][0]["value"]
    assert changed["evidence"] == bundle["evidence"]
    other = deepcopy(bundle)
    other["claims"][0]["value"]["student_scope_verified"] = False
    with pytest.raises(ValueError, match="immutable base"):
        apply_review_patch(other, patch)
    patch["reviews"]["not-in-input"] = {}
    with pytest.raises(ValueError, match="unknown claim"):
        apply_review_patch(bundle, patch)


def test_check_only_cannot_drop_its_reconstructable_input(tmp_path):
    bundle = source_bundle(tmp_path)
    with pytest.raises(ValueError, match="immutable input reference"):
        execute_handoff(bundle, tmp_path / "state", check_only=True)
    with pytest.raises(ValueError, match="Input reference"):
        execute_handoff(bundle, tmp_path / "state", check_only=True, input_ref={"semantic_bundle_sha256": "0" * 64})


def test_projection_audit_detects_unapproved_rows_and_lost_relations(tmp_path):
    from benefitlens_handoff import review_mapping, to_delivery_rows
    bundle = source_bundle(tmp_path)
    review = review_mapping(bundle)
    rows = to_delivery_rows(bundle, Path("/"))
    assert all(projection_audit(bundle, rows, review).values())
    leaked = deepcopy(rows)
    leaked["claim"].append(rows["claim"][0] | {"claim_id": "not-approved"})
    with pytest.raises(ValueError, match="Projection audit"):
        projection_audit(bundle, leaked, review)
    rows["claim_evidence"] = []
    with pytest.raises(ValueError, match="Projection audit"):
        projection_audit(bundle, rows, review)


def test_replay_schema_keeps_legacy_readable_without_claiming_fresh_approval():
    from jsonschema import Draft202012Validator
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "pipeline/schemas/pipeline-run.schema.json").read_text())["properties"]["model_reuse"]
    validator = Draft202012Validator(schema)
    old = {"program_id": "p", "input_sha256": "a" * 64, "model": "archived",
           "original_model_run_at": "2026-10-15T00:00:00+00:00", "documents": 1, "characters": 10, "cases": 1, "patterns": 1}
    assert not list(validator.iter_errors([old] * 3))
    current = old | {"prepared_context_sha256": "b" * 64, "prompt_version": "whole-experience-evidence-v2", "extractor": "llm_full_experience_v2",
        "scope_validation": "legacy_v2_requires_scope_review", "approved_handoff": False, "semantic_approval_executed": False,
        "compatibility_labels_added": [], "model_called_this_run": False}
    assert not list(validator.iter_errors([current] * 3))
    assert list(validator.iter_errors([current | {"approved_handoff": True}] * 3))
