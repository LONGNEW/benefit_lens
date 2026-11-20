from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_handoff import review_mapping, validate_handoff
from benefitlens_semantic_expansion import (VERSION, apply_semantic_patch, canonical,
    generate_semantic_patch, prune_known_aliases, write_semantic_patch)


@pytest.fixture
def bundle(tmp_path):
    text = "Student Scholarship\nScholarship value\n100% tuition fee waiver, plus $500 annual living stipend\n"
    (tmp_path / "raw").write_bytes(text.encode())
    (tmp_path / "text").write_text(text)
    digest = sha256(text.encode()).hexdigest()
    return {"contract_version": "team-handoff-v1", "bundle_id": "synthetic", "producer": "test",
        "created_at": "2026-11-09T00:00:00+00:00", "metadata": {"example_kind": "synthetic",
        "source_specific": [{"program_id": "p", "round_id": "r", "fields": {"source_family": "UQ", "country": "AU"}}]},
        "programs": [{"program_id": "p", "title": "Student Scholarship", "issuer": "Fixture", "opportunity_type": "other"}],
        "opportunity_rounds": [{"program_id": "p", "round_id": "r", "opportunity_id": "p-r", "notice_version": "v",
            "status": "open", "official_url": "https://example.org", "application_url": None, "opens_on": None,
            "closes_on": None, "announces_on": None, "checked_on": "2026-11-09"}],
        "source_documents": [{"document_id": "d", "title": "Fixture", "url": "https://example.org", "kind": "text",
            "capture_mode": "full_document", "fetched_at": "2026-11-09T00:00:00+00:00", "raw_sha256": digest,
            "text_sha256": digest, "raw_path": str(tmp_path / "raw"), "text_path": str(tmp_path / "text"), "access_scope": "public"}],
        "evidence": [{"evidence_id": "e", "document_id": "d", "program_id": "p", "round_id": "r", "source_role": "official_notice",
                      "locator": "L1", "quote": "Student Scholarship"}],
        "claims": [], "experience_cases": [], "case_documents": [], "writing_patterns": [], "pattern_evidence": []}


def spec():
    return {"program_id": "p", "round_id": "r", "document_id": "d", "quote": "Student Scholarship", "locator": "L1",
            "kind": "advice", "field": "opportunity_classification", "at": "source_observation",
            "value": {"schema_version": VERSION, "opportunity_form": "scholarship"}, "rule_id": "fixture-reviewed-title",
            "rule_version": "1", "reason": "Controlled reviewed fixture, not a real approval"}


def reviewed(bundle, specifications=None):
    return generate_semantic_patch(bundle, reviewed_mappings=[spec()] if specifications is None else specifications,
                                   review_receipt_ref="fixture://review")


def test_extraction_rules_only_propose_and_do_not_infer_nationality(bundle):
    patch = generate_semantic_patch(bundle)
    assert patch["summary"]["mapping_states"] == {"candidate": 1}
    assert patch["claims"] == patch["program_updates"] == []
    assert "nationality" not in canonical(patch)


def test_reviewed_delta_is_applied_without_replacing_or_mutating_the_base(bundle):
    original = deepcopy(bundle)
    patch = reviewed(bundle)
    result = apply_semantic_patch(bundle, patch)
    assert bundle == original and result["programs"] == bundle["programs"]
    assert result["programs"][0]["opportunity_type"] == "other"
    assert result["metadata"]["normalized_classification_overlay"][0]["opportunity_form"] == "scholarship"
    assert validate_handoff(result, artifact_root="/")["valid"]
    assert len(review_mapping(result)["accepted"]) == 1
    assert result["source_documents"] is bundle["source_documents"]


def test_patch_replay_is_idempotent_and_additive_upstream_records_are_allowed(bundle):
    patch = reviewed(bundle)
    result = apply_semantic_patch(bundle, patch)
    assert canonical(apply_semantic_patch(result, patch)) == canonical(result)
    changed = deepcopy(bundle)
    changed["metadata"]["upstream_additive_delta"] = "new unrelated records"
    assert len(apply_semantic_patch(changed, patch)["claims"]) == 1


def test_changed_anchor_or_conflicting_immutable_row_is_rejected(bundle):
    patch = reviewed(bundle)
    changed = deepcopy(bundle)
    changed["source_documents"][0]["text_sha256"] = "1" * 64
    with pytest.raises(ValueError, match="anchor changed"):
        apply_semantic_patch(changed, patch)
    changed = apply_semantic_patch(bundle, patch)
    changed["claims"][0]["value"]["opportunity_form"] = "internship"
    with pytest.raises(ValueError, match="Conflicting immutable"):
        apply_semantic_patch(changed, patch)


def test_title_internship_obligation_is_not_a_second_fellowship_opportunity(bundle):
    bundle["programs"][0]["title"] = "Research Fellowship"
    path = Path(bundle["source_documents"][0]["text_path"])
    text = "Research Fellowship\nA summer internship is required before the award year.\n"
    path.write_text(text)
    bundle["source_documents"][0]["text_sha256"] = sha256(text.encode()).hexdigest()
    patch = generate_semantic_patch(bundle)
    assert patch["mapping_decisions"][0]["proposed_form"] == "fellowship"
    assert not patch["claims"]


def test_wrong_quote_hash_and_cross_variant_scope_cannot_be_reviewed(bundle):
    bad = spec() | {"quote": "Not actually in this source"}
    with pytest.raises(ValueError, match="exact original"):
        reviewed(bundle, [bad])
    bad = spec() | {"round_id": "another"}
    with pytest.raises(ValueError, match="exact-round"):
        reviewed(bundle, [bad])
    Path(bundle["source_documents"][0]["raw_path"]).write_text("Changed bytes")
    with pytest.raises(ValueError, match="hash mismatch"):
        reviewed(bundle)


def test_dollar_symbol_does_not_establish_usd_and_payment_method_is_separate(bundle):
    support = {"schema_version": "common-field-v1", "component_id": "living", "purpose": "living", "method": "stipend",
        "beneficiary": "student", "conditions": [], "conditions_state": "not_reviewed",
        "amount": {"kind": "exact", "value": 500, "min": None, "max": None, "currency_code": "USD", "raw_symbol": "$",
                   "frequency": "per_year", "currency_evidence_ids": [], "gross_or_net": "unknown"}}
    s = spec() | {"kind": "benefit", "field": "support_component", "quote": "$500 annual living stipend",
                  "locator": "L3", "value": support, "currency_quote": "$"}
    with pytest.raises(ValueError, match="Currency normalization"):
        reviewed(bundle, [s])
    support["amount"]["currency_code"] = None
    assert len(reviewed(bundle, [s])["claims"]) == 1
    support["method"] = "internship"
    with pytest.raises(ValueError, match="Invalid typed"):
        reviewed(bundle, [s])


def test_byte_budget_and_immutable_output_are_enforced(bundle, tmp_path):
    patch = reviewed(bundle)
    path = tmp_path / "patch.json"
    with pytest.raises(ValueError, match="byte budget"):
        write_semantic_patch(patch, path, max_bytes=10)
    assert not path.exists()
    first = write_semantic_patch(patch, path)
    assert write_semantic_patch(patch, path) == first
    with pytest.raises(ValueError, match="Immutable patch"):
        write_semantic_patch(patch | {"scope": "different"}, path)


def test_carried_mapping_is_an_alias_only_in_the_exact_source_and_variant(bundle):
    patch = reviewed(bundle)
    existing = spec()
    alias = prune_known_aliases(patch, [existing])
    assert not alias["claims"] and len(alias["aliases"]) == 1
    for key, changed in (("round_id", "another"), ("document_id", "another"), ("at", "activity_period")):
        distinct = existing | {key: changed}
        assert len(prune_known_aliases(patch, [distinct])["claims"]) == 1


def test_unit_or_payment_method_differences_do_not_disappear_in_aliasing(bundle):
    s = spec() | {"kind": "benefit", "field": "plain_payment_extension", "at": None,
                  "value": {"amount": 500, "frequency": "per_year", "method": "stipend"}}
    patch = reviewed(bundle, [s])
    assert not prune_known_aliases(patch, [s])["claims"]
    for value in ({"amount": 500, "frequency": "per_month", "method": "stipend"},
                  {"amount": 500, "frequency": "per_year", "method": "loan"}):
        assert len(prune_known_aliases(patch, [s | {"value": value}])["claims"]) == 1
