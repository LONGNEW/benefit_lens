"""No network, model call, production DB or real applicant data are used."""
from copy import deepcopy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_writing_handoff import (
    WritingHandoffError, build_writing_handoff, writing_scope_fingerprint,
)
from benefitlens_handoff import claim_scope_fingerprint


def review():
    return {'state': 'accepted', 'reviewer': 'fixture-agent', 'decision_id': 'review-1',
            'reason': 'Synthetic fixture review, not an observed applicant.',
            'basis': 'agent_semantic_review', 'review_receipt_ref': '/fixture/review.json'}


def document(identifier, **extra):
    return dict(document_id=identifier, access_scope='public', title=identifier,
                url='https://example.org/' + identifier, kind='html', capture_mode='full_document',
                fetched_at='2026-11-09T00:00:00Z', raw_sha256='a'*64, text_sha256='b'*64,
                raw_path='/fixture/' + identifier, text_path='/fixture/' + identifier + '.txt', **extra)


def span(eid, did, role, **extra):
    return dict(evidence_id=eid, document_id=did, program_id='fellowship', round_id='2027',
                source_role=role, quote='Synthetic source excerpt', locator='L1-L2', **extra)


def bundle():
    b = {'contract_version': 'team-handoff-v1', 'bundle_id': 'fixture', 'producer': 'test',
         'created_at': '2026-11-09T00:00:00Z', 'metadata': {'subject_id': 'synthetic-student'},
         'programs': [{'program_id': 'fellowship'}],
         'opportunity_rounds': [{'program_id': 'fellowship', 'round_id': '2027', 'opportunity_id': 'opp'}],
         'source_documents': [document('official'), document('statement')],
         'evidence': [span('prompt-source', 'official', 'official_notice'),
                      span('case-source', 'statement', 'application_statement')],
         'claims': [{'claim_id': 'proposal', 'program_id': 'fellowship', 'round_id': '2027',
                     'kind': 'submission', 'field': 'research_proposal', 'value': {'max_pages': 2},
                     'review_state': 'reviewed', 'evidence_ids': ['prompt-source'], 'semantic_review': review()}],
         'experience_cases': [{'case_id': 'C1', 'program_id': 'fellowship', 'experience_round_id': None,
                               'outcome_status': 'unknown', 'outcome_basis': 'unknown'}],
         'case_documents': [{'case_id': 'C1', 'document_id': 'statement', 'program_id': 'fellowship',
                             'document_role': 'application_statement', 'finality': 'unknown',
                             'binding_evidence_id': 'case-source'}],
         'writing_patterns': [{'pattern_id': 'pattern', 'program_id': 'fellowship', 'target_round_id': '2027',
             'target_claim_id': 'proposal', 'pattern_kind': 'structure', 'scope_status': 'historical_transfer',
             'body': {'title': 'Problem, own action, next test', 'scope_limit': 'Unverified public statement only.'},
             'causal_success_claim': False, 'semantic_review': review()}],
         'pattern_evidence': [{'pattern_id': 'pattern', 'program_id': 'fellowship', 'target_round_id': '2027',
             'evidence_id': 'case-source', 'document_id': 'statement', 'source_program_id': 'fellowship',
             'source_round_id': '2027', 'case_id': 'C1', 'case_document_role': 'application_statement',
             'purpose': 'case_observation'}]}
    stamp(b)
    return b


def stamp(b):
    for claim in b['claims']:
        claim['semantic_review']['input_scope_fingerprint'] = claim_scope_fingerprint(b, claim)
    for pattern in b['writing_patterns']:
        pattern['semantic_review']['input_scope_fingerprint'] = writing_scope_fingerprint(b, pattern)


def result(b, facts=None, needs=None):
    return build_writing_handoff(b, facts, target_opportunity_id='opp',
        required_fields_by_claim=needs or {'proposal': ['action', 'result']})['metadata']['writing_result']


def add_fact(b, fid, field, value, *, experience='E1', subject='synthetic-student'):
    doc = document(fid + '-private')
    doc.update(access_scope='user_private', owner_subject_id=subject, permission_basis='user_provided')
    b['source_documents'].append(doc)
    b['evidence'].append(span(fid + '-evidence', doc['document_id'], 'other', experience_id=experience))
    return {'fact_id': fid, 'subject_id': subject, 'experience_id': experience, 'field': field,
            'value': value, 'review_status': 'accepted', 'eligible_for_final_use': True,
            'derivation_type': 'user_confirmed', 'evidence_ids': [fid + '-evidence'],
            'semantic_review': review()}


def test_pure_adapter_prepares_official_checklist_without_approving_new_patterns():
    b = bundle()
    before = deepcopy(b)
    output = result(b)
    assert b == before
    assert output['accepted_pattern_ids'] == ['pattern']
    assert output['preparation_checklist'][0]['official_value'] == {'max_pages': 2}
    assert output['draft_slots'][0]['missing_fields'] == ['action', 'result']
    assert output['draft_slots'][0]['draft_text'] is None
    assert output['llm_called'] is False and output['is_finished_application'] is False
    assert len(b['writing_patterns']) == 1


def test_unbound_legacy_pattern_is_held_but_official_prompt_still_works():
    b = bundle()
    b['writing_patterns'][0]['semantic_review'].pop('input_scope_fingerprint')
    output = result(b)
    assert output['accepted_pattern_ids'] == []
    assert output['guidance'][0]['guidance_id'] == 'official:proposal'
    assert output['held'][0]['reason'] == 'input_scope_fingerprint_missing_or_changed'


@pytest.mark.parametrize('mutation', ['role', 'finality', 'round', 'quote', 'outcome'])
def test_old_review_cannot_survive_scope_relabel(mutation):
    b = bundle()
    if mutation == 'role':
        b['case_documents'][0]['document_role'] = 'interview'
    if mutation == 'finality':
        b['case_documents'][0]['finality'] = 'confirmed_final'
    if mutation == 'round':
        b['experience_cases'][0]['experience_round_id'] = '2026'
    if mutation == 'quote':
        b['evidence'][1]['quote'] = 'Different source excerpt'
    if mutation == 'outcome':
        b['experience_cases'][0]['outcome_status'] = 'selected'
    assert result(b)['accepted_pattern_ids'] == []


def test_fingerprint_is_stable_under_unrelated_record_order():
    b = bundle()
    digest = writing_scope_fingerprint(b, b['writing_patterns'][0])
    b['source_documents'].reverse()
    b['evidence'].reverse()
    assert writing_scope_fingerprint(b, b['writing_patterns'][0]) == digest


def test_unknown_finality_is_valid_for_scoped_observation_but_not_final_comparison():
    b = bundle()
    assert result(b)['accepted_pattern_ids'] == ['pattern']
    b['writing_patterns'][0]['body']['comparison_scope'] = 'same_prompt_final_statements'
    stamp(b)
    assert result(b)['accepted_pattern_ids'] == []


@pytest.mark.parametrize('damage', ['candidate', 'wrong_round', 'nonofficial', 'no_receipt', 'access_unknown'])
def test_unreviewed_or_wrong_scope_submission_cannot_make_a_draft_slot(damage):
    b = bundle()
    if damage == 'candidate':
        b['claims'][0]['semantic_review']['state'] = 'candidate'
    if damage == 'wrong_round':
        b['evidence'][0]['round_id'] = '2026'
    if damage == 'nonofficial':
        b['evidence'][0]['source_role'] = 'application_statement'
    if damage == 'no_receipt':
        b['claims'][0]['semantic_review'].pop('review_receipt_ref')
    if damage == 'access_unknown':
        b['source_documents'][0].pop('access_scope')
    assert result(b)['draft_slots'] == []


def test_submission_route_and_recommendation_are_checklist_not_writing_prompts():
    b = bundle()
    for name in ['application_route', 'document_requirement', 'recommendation_letters']:
        c = deepcopy(b['claims'][0])
        c.update(claim_id=name, field=name, value={'required': True})
        b['claims'].append(c)
    stamp(b)
    output = result(b)
    assert len(output['preparation_checklist']) == 4
    assert {s['target_claim_id'] for s in output['draft_slots']} == {'proposal'}


def test_fields_from_different_experiences_are_never_combined_to_complete_one_slot():
    b = bundle()
    action = add_fact(b, 'f1', 'action', 'Changed the parser', experience='E1')
    observed = add_fact(b, 'f2', 'result', 'Observed 12 valid rows', experience='E2')
    output = result(b, [action, observed])
    slots = {s['experience_id']: s for s in output['draft_slots']}
    assert slots['E1']['fact_ids'] == ['f1'] and slots['E1']['missing_fields'] == ['result']
    assert slots['E2']['fact_ids'] == ['f2'] and slots['E2']['missing_fields'] == ['action']
    assert all(s['status'] == 'needs_user_facts' for s in slots.values())


@pytest.mark.parametrize('damage', ['foreign_owner', 'candidate_fact', 'semantic_inference', 'public_example', 'wrong_experience'])
def test_applicant_facts_need_ownership_review_and_same_experience_provenance(damage):
    b = bundle()
    fact = add_fact(b, 'f1', 'action', 'My real action')
    if damage == 'foreign_owner':
        b['source_documents'][-1]['owner_subject_id'] = 'someone-else'
    if damage == 'candidate_fact':
        fact['review_status'] = 'candidate'
    if damage == 'semantic_inference':
        fact['derivation_type'] = 'semantic_inference'
    if damage == 'public_example':
        fact['evidence_ids'] = ['case-source']
    if damage == 'wrong_experience':
        b['evidence'][-1]['experience_id'] = 'E2'
    output = result(b, [fact])
    assert output['draft_slots'][0]['fact_ids'] == []
    assert any(h['kind'] == 'applicant_fact' for h in output['held'])


def test_confirmed_same_experience_facts_are_ready_but_no_llm_draft_is_claimed():
    b = bundle()
    facts = [add_fact(b, 'f1', 'action', 'Changed parser'), add_fact(b, 'f2', 'result', 'Observed 12 valid rows')]
    output = result(b, facts)
    assert output['draft_slots'][0]['status'] == 'facts_ready_for_drafting'
    assert output['draft_slots'][0]['fact_ids'] == ['f1', 'f2']
    assert output['draft_slots'][0]['draft_text'] is None


def test_conflicting_confirmed_values_require_a_question_and_derived_parents_cannot_cross_experiences():
    b = bundle()
    f1 = add_fact(b, 'f1', 'action', 'Value A')
    f2 = add_fact(b, 'f2', 'action', 'Value B')
    f3 = add_fact(b, 'f3', 'result', 10, experience='E2')
    f3.update(derivation_type='rule_derived', derived_from_fact_ids=['f1'], derivation_rule='count')
    output = result(b, [f1, f2, f3])
    assert output['draft_slots'][0]['fact_ids'] == []
    assert any(q['reason'] == 'conflicting_facts' for q in output['missing_questions'])
    assert any(h['id'] == 'f3' for h in output['held'])


def test_no_writing_needs_are_invented_from_a_prompt_name():
    b = bundle()
    output = build_writing_handoff(b)['metadata']['writing_result']
    assert output['draft_slots'] == []
    assert output['missing_questions'][0]['reason'] == 'writing_needs_not_specified'
    with pytest.raises(WritingHandoffError, match='Target opportunity'):
        build_writing_handoff(b, target_opportunity_id='other')


def test_same_evidence_does_not_allow_changed_official_prompt_to_reuse_review():
    b = bundle()
    b['claims'][0]['value']['max_pages'] = 20
    output = result(b)
    assert output['preparation_checklist'] == []
    assert output['draft_slots'] == []
    assert output['held'][0]['reason'] == 'claim_input_scope_fingerprint_missing_or_changed'


def test_ready_fact_slots_are_deterministic_and_do_not_change_claim_fingerprints():
    b = bundle()
    facts = [add_fact(b, 'f1', 'action', 'Changed parser')]
    first = build_writing_handoff(b, facts, required_fields_by_claim={'proposal': ['action']})
    second = build_writing_handoff(b, facts, required_fields_by_claim={'proposal': ['action']})
    assert first == second
    assert claim_scope_fingerprint(first, first['claims'][0]) == claim_scope_fingerprint(b, b['claims'][0])
