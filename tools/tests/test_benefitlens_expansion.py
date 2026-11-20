from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_handoff import claim_scope_fingerprint
from run_benefitlens_expansion import (VERSION, compose_manifest, current_rounds,
    family_metrics, file_ref, merge_bundle, pinned_json, regroup_families, apply_family_reviews)
from test_benefitlens_handoff_flow import source_bundle


def save(path, value):
    path.write_text(json.dumps(value))
    return file_ref(path)


def manifest(tmp_path, base, deltas=()):
    return {"contract_version": VERSION, "as_of": "2026-11-09",
        "baseline": save(tmp_path / "base.json", base), "deltas": list(deltas)}


def approved_current(tmp_path):
    bundle = source_bundle(tmp_path)
    bundle["opportunity_rounds"][0].update(status="open", application_url="https://example.org/apply")
    scope = bundle["claims"][0]
    scope["value"] = {"student_scope_verified": True}
    status = deepcopy(scope)
    status.update(claim_id="status", kind="schedule", field="current_application_status",
                  value={"status": "open", "application_period_verified": True})
    period = deepcopy(status)
    period.update(claim_id="period", field="verified_application_period",
                  value={"application_period": {"verification_state": "verified"}})
    bundle["claims"] = [scope, status, period]
    bundle["metadata"]["source_specific"] = [{"program_id": "p", "round_id": "r", "fields": {
        "issuer": "Fixture University", "title": "Data Engineering Research Intern", "source_family": "fixture", "native_id": "r"}}]
    for claim in bundle["claims"]:
        claim["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(bundle, claim)
    return bundle


def test_hash_pins_prevent_input_changes_and_relative_paths(tmp_path):
    ref = save(tmp_path / "input.json", {"a": 1})
    assert pinned_json(ref) == {"a": 1}
    (tmp_path / "input.json").write_text('{"a":2}')
    with pytest.raises(ValueError, match="hash mismatch"):
        pinned_json(ref)
    with pytest.raises(ValueError, match="absolute"):
        pinned_json({"path": "input.json", "sha256": "0" * 64})


def family_review_fixture(tmp_path):
    bundle=approved_current(tmp_path)
    period=next(c for c in bundle['claims'] if c['field']=='verified_application_period')
    raw=bundle['source_documents'][0]['raw_sha256']
    period['value']['posting']={'raw_sha256':raw}
    period['semantic_review']['input_scope_fingerprint']=claim_scope_fingerprint(bundle,period)
    bundle=regroup_families(bundle,{('p','r')})
    group=bundle['metadata']['family_memberships'][0]['opportunity_family_id']
    patch=[{'review_id':'content-duplicate','reason':'Same issuer and same original body; review titles',
            'evidence_ref':save(tmp_path/'content-review.json',{'body_comparison':'exact'}),
            'variants':[{'program_id':'p','round_id':'r','native_id':'r','source_family':'fixture',
                         'raw_sha256':raw,'group_id':group}]}]
    return bundle,patch


def test_family_review_only_lowers_confidence_without_changing_claims_or_identity(tmp_path):
    bundle,patch=family_review_fixture(tmp_path);before=deepcopy(bundle)
    result=apply_family_reviews(bundle,[save(tmp_path/'family-reviews.json',patch)])
    assert bundle==before and result['claims']==before['claims']
    assert result['metadata']['family_memberships'][0]['opportunity_family_id']==patch[0]['variants'][0]['group_id']
    assert family_metrics(result,{('p','r')})['review_families']==1
    assert family_metrics(result,{('p','r')})['rule_passed_families']==0
    assert all(c['semantic_review']['input_scope_fingerprint']==claim_scope_fingerprint(result,c) for c in result['claims'])


@pytest.mark.parametrize('field,value',[('native_id','another'),('raw_sha256','0'*64),('group_id','another')])
def test_family_review_cannot_borrow_another_record_or_source(tmp_path,field,value):
    bundle,patch=family_review_fixture(tmp_path);patch[0]['variants'][0][field]=value
    with pytest.raises(ValueError,match='scope mismatch'):
        apply_family_reviews(bundle,[save(tmp_path/'bad-family-reviews.json',patch)])


def test_family_review_cannot_be_dropped_in_a_later_manifest(tmp_path):
    bundle,patch=family_review_fixture(tmp_path)
    first=manifest(tmp_path,bundle)
    first['family_review_overrides']=[save(tmp_path/'family-reviews.json',patch)]
    second=deepcopy(first);second['comparison_manifest']=save(tmp_path/'previous.json',first)
    second['family_review_overrides']=[]
    with pytest.raises(ValueError,match='drops prior family reviews'):
        compose_manifest(second)


def test_merge_collapses_exact_rows_but_preserves_inputs(tmp_path):
    bundle = source_bundle(tmp_path)
    before = deepcopy(bundle)
    merged, counts = merge_bundle(bundle, bundle)
    assert bundle == before
    assert counts["claims"] == 1 and len(merged["claims"]) == 1
    merged["metadata"]["new"] = True
    assert "new" not in bundle["metadata"]


@pytest.mark.parametrize("array,field,value", [
    ("source_documents", "text_sha256", "a" * 64),
    ("claims", "value", {"student_scope_verified": False}),
    ("opportunity_rounds", "status", "open"),
])
def test_same_identity_never_overwrites_different_content(tmp_path, array, field, value):
    bundle = source_bundle(tmp_path)
    other = deepcopy(bundle)
    other[array][0][field] = value
    with pytest.raises(ValueError, match="Conflicting expansion identity"):
        merge_bundle(bundle, other)


def test_same_employer_role_variants_count_one_parent(tmp_path):
    bundle = source_bundle(tmp_path)
    bundle["metadata"]["family_memberships"] = [
        {"program_id": "p", "round_id": "r", "opportunity_family_id": "same-employer-role", "review_state": "confirmed"},
        {"program_id": "p", "round_id": "other-country", "opportunity_family_id": "same-employer-role", "review_state": "confirmed"},
    ]
    metrics = family_metrics(bundle, {("p", "r"), ("p", "other-country")})
    assert metrics["parent_family_candidates"] == 1
    assert metrics["rule_passed_families"] == 1
    bundle["metadata"]["family_memberships"][1]["review_state"] = "review_required"
    assert family_metrics(bundle, {("p", "r"), ("p", "other-country")})["rule_passed_families"] == 0


def test_historical_writing_does_not_raise_current_count(tmp_path):
    bundle = source_bundle(tmp_path)
    data = manifest(tmp_path, bundle)
    data["deltas"] = [save(tmp_path / "history.json", bundle) | {"role": "historical_writing"}]
    result, required, summary = compose_manifest(data)
    assert summary["current_rule_verified_variants"] == 0
    assert summary["new_current_variants"] == 0
    assert required == {}
    assert len(result["opportunity_rounds"]) == 1


def test_history_cannot_smuggle_an_open_call(tmp_path):
    bundle = approved_current(tmp_path)
    data = manifest(tmp_path, bundle)
    data["deltas"] = [save(tmp_path / "history.json", bundle) | {"role": "historical_writing"}]
    with pytest.raises(ValueError, match="Historical writing"):
        compose_manifest(data)


def test_current_count_requires_positive_values_current_date_and_fresh_approvals(tmp_path):
    bundle = approved_current(tmp_path)
    assert current_rounds(bundle, "2026-11-09") == {("p", "r")}
    assert not current_rounds(bundle, "2026-11-10")
    bundle["claims"][0]["value"]["student_scope_verified"] = False
    # Even a separately scoped approval of a negative fact cannot make it a student opportunity.
    claim = bundle["claims"][0]
    claim["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(bundle, claim)
    assert not current_rounds(bundle, "2026-11-09")


def test_pending_new_current_input_is_not_counted_as_acquired(tmp_path):
    base = source_bundle(tmp_path)
    extra = deepcopy(base)
    extra["opportunity_rounds"][0].update(round_id="unparsed-new", opportunity_id="p-unparsed-new")
    data = manifest(tmp_path, base)
    data["deltas"] = [save(tmp_path / "extra.json", extra) | {"role": "current_opportunities"}]
    with pytest.raises(ValueError, match="lacks approved"):
        compose_manifest(data)


def test_conflicting_private_subjects_are_separate_runs(tmp_path):
    base = source_bundle(tmp_path)
    extra = deepcopy(base)
    base["metadata"]["subject_id"] = "student-one"
    extra["metadata"]["subject_id"] = "student-two"
    with pytest.raises(ValueError, match="private subjects"):
        merge_bundle(base, extra)


def test_required_fields_conflict_does_not_silently_override(tmp_path):
    base = source_bundle(tmp_path)
    data = manifest(tmp_path, base)
    data["required_fields"] = [save(tmp_path / "one.json", {"c": ["one"]}), save(tmp_path / "two.json", {"c": ["two"]})]
    with pytest.raises(ValueError, match="writing input requirements"):
        compose_manifest(data)


def test_combined_grouping_catches_cross_input_near_duplicate_roles(tmp_path):
    base = approved_current(tmp_path)
    base["metadata"]["source_specific"].append({"program_id": "p2", "round_id": "r2", "fields": {
        "issuer": "Fixture University", "title": "Data Engineering Research Interns", "source_family": "fixture", "native_id": "r2"}})
    result = regroup_families(base, {("p", "r"), ("p2", "r2")})
    metrics = family_metrics(result, {("p", "r"), ("p2", "r2")})
    assert metrics["rule_passed_families"] == 0
    assert metrics["review_families"] == 2


@pytest.mark.parametrize("field,value,expected", [
    ("closes_on", "2026-11-08", False), ("closes_on", "2026-11-09", True),
    ("opens_on", "2026-11-10", False), ("status", "closed", False),
])
def test_current_day_boundaries_are_independent_of_review_freshness(tmp_path, field, value, expected):
    bundle = approved_current(tmp_path)
    bundle["opportunity_rounds"][0][field] = value
    for claim in bundle["claims"]:
        claim["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(bundle, claim)
    assert bool(current_rounds(bundle, "2026-11-09")) is expected


def test_regrouping_does_not_infer_missing_original_source_fields(tmp_path):
    bundle = source_bundle(tmp_path)
    with pytest.raises(ValueError, match="original source fields"):
        regroup_families(bundle, {("p", "r")})


def test_one_variant_cannot_count_in_two_parent_families(tmp_path):
    bundle = source_bundle(tmp_path)
    link = {"program_id": "p", "round_id": "r", "opportunity_family_id": "one", "review_state": "confirmed"}
    bundle["metadata"]["family_memberships"] = [link, link | {"opportunity_family_id": "two"}]
    with pytest.raises(ValueError, match="Conflicting family membership"):
        family_metrics(bundle, {("p", "r")})


def test_historical_delta_cannot_relabel_a_baseline_current_round(tmp_path):
    bundle = approved_current(tmp_path)
    extra = deepcopy(bundle)
    extra["opportunity_rounds"][0]["status"] = "historical"
    data = manifest(tmp_path, bundle)
    data["deltas"] = [save(tmp_path / "relabeled.json", extra) | {"role": "historical_writing"}]
    with pytest.raises(ValueError, match="baseline current round"):
        compose_manifest(data)


def test_prior_reviewed_common_fields_are_carried_forward_without_changing_old_claims(tmp_path):
    base = source_bundle(tmp_path)
    data = manifest(tmp_path, base)
    spec = {"program_id": "p", "round_id": "r", "document_id": "snapshot", "kind": "eligibility", "field": "enrollment_maintenance",
        "at": "activity_period", "quote": "Students remain enrolled during the internship.", "reason": "Previously reviewed synthetic condition",
        "value": {"schema_version": "common-field-v1", "enrollment_status": "enrolled", "full_time": False, "throughout": True}}
    data["enrichments"] = [save(tmp_path / "enrichments.json", [spec]) | {
        "reviewer": "fixture-agent", "review_receipt": save(tmp_path / "review.json", {"scope": "synthetic test"})}]
    result, _, _ = compose_manifest(data)
    assert len(result["claims"]) == 2
    assert result["claims"][0] == base["claims"][0]
    assert result["claims"][1]["field"] == "enrollment_maintenance"


def test_misspelled_manifest_stage_cannot_silently_skip_expansion(tmp_path):
    data = manifest(tmp_path, source_bundle(tmp_path))
    data["delats"] = data.pop("deltas")
    with pytest.raises(ValueError, match="Unknown expansion manifest fields"):
        compose_manifest(data)


def test_audit_receipt_hash_is_pinned_too(tmp_path):
    data = manifest(tmp_path, source_bundle(tmp_path))
    data["audit_inputs"] = [save(tmp_path / "review.json", {"approved": True})]
    (tmp_path / "review.json").write_text('{"approved":false}')
    with pytest.raises(ValueError, match="Pinned input hash mismatch"):
        compose_manifest(data)


def test_semantic_patch_can_target_a_new_delta_after_composition(tmp_path):
    from benefitlens_semantic_expansion import generate_semantic_patch
    base = source_bundle(tmp_path)
    extra = deepcopy(base)
    extra["programs"][0]["program_id"] = "p2"
    extra["opportunity_rounds"][0].update(program_id="p2", round_id="r2", opportunity_id="p2-r2")
    extra["evidence"][0].update(program_id="p2", round_id="r2", evidence_id="e2")
    extra["claims"][0].update(program_id="p2", round_id="r2", claim_id="c2", evidence_ids=["e2"])
    extra["claims"][0]["semantic_review"]["input_scope_fingerprint"] = claim_scope_fingerprint(extra, extra["claims"][0])
    spec = {"program_id": "p2", "round_id": "r2", "document_id": "snapshot", "kind": "eligibility", "field": "enrollment_maintenance",
        "at": "activity_period", "quote": "Students remain enrolled during the internship.", "reason": "Synthetic new source mapping",
        "rule_id": "synthetic-expansion", "rule_version": "1",
        "value": {"schema_version": "common-field-v1", "enrollment_status": "enrolled", "full_time": False, "throughout": True}}
    patch = generate_semantic_patch(extra, reviewed_mappings=[spec], review_receipt_ref="synthetic-review")
    data = manifest(tmp_path, base, [save(tmp_path / "delta.json", extra) | {"role": "historical_writing"}])
    data["semantic_patches"] = [save(tmp_path / "semantic.json", patch)]
    result, _, _ = compose_manifest(data)
    assert any(c["program_id"] == "p2" and c["field"] == "enrollment_maintenance" for c in result["claims"])
    assert result["claims"][0] == base["claims"][0]


def comparison_fixture(tmp_path, monkeypatch):
    # Period parsing has separate original-source integration tests. This fixture
    # isolates reporting lineage and deliberately contains only count-gate facts.
    monkeypatch.setattr('run_benefitlens_batch.validate_open_cohort', lambda *args: None)
    current = approved_current(tmp_path)
    empty = deepcopy(current)
    from benefitlens_handoff import ARRAYS
    for name in ARRAYS: empty[name] = []
    empty['metadata'] = {}
    prior = manifest(tmp_path, empty, [save(tmp_path / 'current.json', current) | {'role':'current_opportunities'}])
    data = deepcopy(prior)
    data['comparison_manifest'] = save(tmp_path / 'previous-manifest.json', prior)
    return data


def test_comparison_manifest_does_not_recount_last_round_additions(tmp_path, monkeypatch):
    data = comparison_fixture(tmp_path, monkeypatch)
    _, _, summary = compose_manifest(data)
    assert summary['reconstruction_baseline_rounds'] == 0
    assert summary['baseline_current_rule_verified_variants'] == 1
    assert summary['current_rule_verified_variants'] == 1
    assert summary['new_current_variants'] == 0
    assert summary['new_current_periods_rechecked_from_originals'] == 0
    assert summary['all_incremental_current_periods_rechecked_from_originals'] == 1
    assert summary['new_parent_family_candidates'] == 0


def test_comparison_manifest_cannot_hide_dropped_prior_sources(tmp_path, monkeypatch):
    data = comparison_fixture(tmp_path, monkeypatch)
    data['deltas'] = []
    with pytest.raises(ValueError, match='drops or changes prior programs'):
        compose_manifest(data)


def test_comparison_manifest_requires_same_date_and_reconstruction_anchor(tmp_path, monkeypatch):
    data = comparison_fixture(tmp_path, monkeypatch)
    data['as_of'] = '2026-11-10'
    # No new current rows should be asserted on this different observation day.
    data['deltas'] = []
    with pytest.raises(ValueError, match='same observation date'):
        compose_manifest(data)
    data['as_of'] = '2026-11-09'
    base = pinned_json(data['baseline'])
    data['baseline'] = save(tmp_path / 'different-base.json', base)
    with pytest.raises(ValueError, match='different reconstruction anchor'):
        compose_manifest(data)


def test_comparison_manifest_preserves_prior_writing_input_requirements(tmp_path):
    prior = manifest(tmp_path, source_bundle(tmp_path))
    prior['required_fields'] = [save(tmp_path / 'needs.json', {'c':['verified_document']})]
    data = deepcopy(prior)
    data['comparison_manifest'] = save(tmp_path / 'previous-manifest.json', prior)
    data['required_fields'] = []
    with pytest.raises(ValueError, match='prior writing requirements'):
        compose_manifest(data)
