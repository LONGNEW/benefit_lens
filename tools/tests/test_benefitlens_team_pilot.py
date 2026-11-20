"""Integration failure boundaries for the source/profile/planning handoff."""
from copy import deepcopy
from hashlib import sha256
import json
import gzip
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_benefitlens_team_pilot import (
    build_result, check_sample, fingerprint, validate_integration,
    verify_source_files,
)


def artifacts():
    sample = {'as_of': '2026-10-15', 'opportunities': [
        {'opportunity_id': 'program-2027', 'program_id': 'program', 'round_id': '2027'}]}
    refresh = {'at': '2026-11-19T12:00:00Z',
        'current_recruitment_verification_performed': False,
        'records': [{'record_id': 'program-2027', 'scope': 'historical_demo',
                     'current_open_verified': False}], 'counts': {'refresh_due': 1}}
    personal = {'as_of': '2026-10-15',
        'network_requests': 0, 'model_calls': 0, 'database_writes': 0,
        'records': [{'opportunity_id': 'program-2027'}],
        'bridge': {'task_spec': {'task_id': 'proposal', 'program_id': 'program',
            'round_id': '2027', 'opportunity_id': 'program-2027',
            'subject_id': 'synthetic-student', 'experience_id': 'project-one',
            'question_text': 'Describe one experience.',
            'source_ref': {'status': 'historical_demo', 'publisher_role': 'official_program'}},
            'confirmed_facts': [{'fact_id': 'role-fact', 'subject_id': 'synthetic-student',
                'experience_id': 'project-one', 'field': 'responsibility', 'value': 'Synthetic project owner',
                'status': 'confirmed', 'source': {'kind': 'synthetic_confirmed_fact',
                    'owner_subject_id': 'synthetic-student', 'evidence_id': 'synthetic-confirmation'}}],
            'template_id': 'experience_evidence', 'style_profile': {}}}
    return sample, refresh, personal


def test_lineage_rejects_notice_sets_that_only_have_the_same_count():
    sample, refresh, personal = artifacts()
    personal['records'][0]['opportunity_id'] = 'different-program-2027'
    with pytest.raises(ValueError, match='personalization_notice_identity_mismatch'):
        build_result(sample, refresh, personal)


def test_program_round_task_cannot_drift_after_matching():
    sample, refresh, personal = artifacts()
    personal['bridge']['task_spec']['round_id'] = '2028'
    assert 'writing_task_program_round_mismatch' in validate_integration(sample, refresh, personal)['errors']


@pytest.mark.parametrize('target', ['notice', 'task'])
def test_historical_input_cannot_claim_a_current_source_check(target):
    sample, refresh, personal = artifacts()
    if target == 'notice':
        refresh['records'][0]['current_open_verified'] = True
    else:
        personal['bridge']['task_spec']['source_ref']['status'] = 'verified_current'
    assert not validate_integration(sample, refresh, personal)['valid']


def test_foreign_experience_stays_held_while_the_plan_requests_own_material():
    sample, refresh, personal = artifacts()
    personal['bridge']['confirmed_facts'][0]['subject_id'] = 'someone-else'
    result = build_result(sample, refresh, personal)
    plan = result['paragraph_plan']
    assert plan['held_facts'] == [{'fact_id': 'role-fact', 'reason': 'different_person'}]
    assert all(not p['evidence_fact_ids'] for p in plan['paragraphs'])
    assert plan['need_info']
    assert not result['paragraph_plan_validation']['ready_for_drafting']
    assert result['technical_contract_passed'] and not result['real_application_ready']


def test_valid_offline_pipeline_does_not_invent_quality_scores_or_submission_readiness():
    sample, refresh, personal = artifacts()
    result = build_result(sample, refresh, personal)
    assert result['integration_validation']['valid']
    assert result['paragraph_plan_validation']['valid']
    assert result['counts']['evaluated_quality_scores'] == 0
    assert not result['real_application_ready']
    assert result['paragraph_plan']['draft_text'] is None


def test_modified_sample_requires_a_new_manifest(tmp_path):
    sample, _, _ = artifacts()
    path, manifest = tmp_path/'input.json', tmp_path/'manifest.json'
    path.write_text(json.dumps(sample))
    manifest.write_text(json.dumps({'sha256': sha256(path.read_bytes()).hexdigest(), 'opportunities': 1}))
    assert check_sample(path, manifest) == sample
    sample['opportunities'][0]['round_id'] = '2028'
    path.write_text(json.dumps(sample))
    with pytest.raises(ValueError, match='sample_manifest_hash_mismatch'):
        check_sample(path, manifest)


def test_schema_or_profile_change_invalidates_execution_identity():
    identity = {'source_hash': 'source-A', 'schema_hash': 'schema-A', 'profile_hash': 'profile-A'}
    for field in ('source_hash', 'schema_hash', 'profile_hash'):
        changed = deepcopy(identity)
        changed[field] += '-changed'
        assert fingerprint(changed) != fingerprint(identity)


def test_retained_compressed_source_must_still_match_before_result_reuse(tmp_path):
    original = b'A preserved official question.'
    raw, text = tmp_path/'source.html.gz', tmp_path/'text.txt'
    raw.write_bytes(gzip.compress(original))
    text.write_bytes(original)
    checksum = sha256(original).hexdigest()
    profile = {'writing_task': {'source_ref': {'raw_path': str(raw), 'path': str(text),
                   'raw_sha256': checksum, 'text_sha256': checksum}}}
    assert len(verify_source_files(profile, [])) == 2
    raw.write_bytes(gzip.compress(b'A modified question.'))
    with pytest.raises(ValueError, match='retained_source_hash_mismatch'):
        verify_source_files(profile, [])
