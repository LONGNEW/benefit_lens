from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_writing_plan import build_paragraph_plan, validate_plan, _template, _slots


def inputs():
    task = {'task_id': 'official-leadership', 'subject_id': 'student-a',
            'experience_id': 'project-1', 'question_text': 'Describe your own contribution.',
            'genre': 'experience_essay', 'constraints': {'max_words': 300},
            'source_ref': {'document_id': 'official-1', 'publisher_role': 'official_program',
                           'status': 'verified_current'}}
    slots = sorted({s for p in _template('experience_evidence')['paragraphs'] for s in _slots(p)})
    facts = [{'fact_id': 'fact-' + slot, 'subject_id': 'student-a', 'experience_id': 'project-1',
              'field': slot, 'value': 'confirmed ' + slot, 'status': 'confirmed',
              'source': {'kind': 'user_confirmation', 'owner_subject_id': 'student-a',
                         'evidence_id': 'confirmation-' + slot}} for slot in slots]
    return task, facts


def test_complete_plan_preserves_evidence_links_without_scoring_or_drafting():
    task, facts = inputs()
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {'first_last_links': True})
    assert plan['status'] == 'plan_ready'
    assert validate_plan(plan, task, facts)['valid']
    assert len(plan['quality_diagnostics']) == 9
    assert all(x['score'] is None for x in plan['quality_diagnostics'])
    assert plan['draft_text'] is None and not plan['approved_pattern']
    assert plan['paragraphs'][0]['next_link']['handoff_slot'] == 'chosen_change'
    assert plan['paragraphs'][1]['previous_link']['handoff_slot'] == 'chosen_change'
    assert not plan['ready_for_real_application']


@pytest.mark.parametrize('change,reason', [
    ({'subject_id': 'student-b'}, 'different_person'),
    ({'experience_id': 'another-project'}, 'different_experience'),
    ({'status': 'unreviewed'}, 'fact_not_confirmed'),
    ({'source': {'kind': 'public_corpus', 'owner_subject_id': 'student-a', 'evidence_id': 'e'}}, 'unsupported_fact_source'),
    ({'source': {'kind': 'user_document', 'owner_subject_id': 'student-b', 'evidence_id': 'e'}}, 'source_owner_mismatch')])
def test_foreign_or_unconfirmed_material_never_fills_the_missing_slot(change, reason):
    task, facts = inputs()
    missing_field = facts[0]['field']
    facts[0].update(change)
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    assert plan['status'] == 'need_info'
    assert any(x['field'] == missing_field for x in plan['need_info'])
    assert plan['held_facts'][0]['reason'] == reason
    assert facts[0]['fact_id'] not in {fid for p in plan['paragraphs'] for fid in p['evidence_fact_ids']}
    assert validate_plan(plan, task, facts)['valid']


def test_conflicting_confirmed_values_request_resolution_instead_of_selecting_one():
    task, facts = inputs()
    extra = deepcopy(facts[0]); extra.update(fact_id='conflicting', value='a different result')
    facts.append(extra)
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    assert any(x['reason'] == 'conflicting_confirmed_values' for x in plan['need_info'])


def test_explicit_fact_id_binding_resolves_a_known_ambiguity():
    task, facts = inputs()
    extra = deepcopy(facts[0]); extra.update(fact_id='conflicting', value='a different result')
    facts.append(extra)
    task['slot_bindings'] = {extra['field']: extra['fact_id']}
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    assert plan['status'] == 'plan_ready' and validate_plan(plan, task, facts)['valid']


def test_official_single_paragraph_limit_blocks_multi_paragraph_preference():
    task, facts = inputs()
    task['constraints']['max_paragraphs'] = 1
    plan = build_paragraph_plan(task, facts, 'experience_evidence',
                                {'first_last_links': True, 'preferred_paragraph_count': 3})
    assert plan['status'] == 'needs_resolution'
    assert not validate_plan(plan, task, facts)['ready_for_drafting']
    tampered = deepcopy(plan); tampered['constraint_conflicts'] = []; tampered['status'] = 'plan_ready'
    assert 'constraint_conflict_diagnostics_changed' in validate_plan(tampered, task, facts)['errors']


def test_interpretation_does_not_become_official_mandatory_requirement():
    task, facts = inputs()
    task['requirements'] = [{'requirement_id': 'r1', 'text': 'Show influence', 'basis': 'inference', 'required': True},
                            {'requirement_id': 'r2', 'text': 'Answer the question', 'basis': 'literal', 'required': True},
                            {'requirement_id': 'r3', 'text': 'Consider impact', 'basis': 'official_guidance', 'required': True}]
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    assert [x['mandatory'] for x in plan['meaning_observations']] == [False, True, False]
    task['source_ref']['status'] = 'historical_demo'
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    assert not any(x['mandatory'] for x in plan['meaning_observations'])


def test_admin_data_collection_does_not_receive_a_forced_persuasive_essay():
    task, facts = inputs()
    task.update(answer_use='data_collection', question_kind='administrative_instruction')
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    assert plan['status'] == 'not_applicable' and plan['paragraphs'] == []
    assert validate_plan(plan, task, facts)['valid']


def test_admin_field_does_not_inherit_essay_style_conflicts():
    task, facts = inputs()
    task.update(answer_use='data_collection', constraints={'max_paragraphs': 1})
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {'preferred_paragraph_count': 3})
    assert plan['constraint_conflicts'] == []
    assert validate_plan(plan, task, facts)['valid']


def test_historical_or_personal_source_cannot_be_hidden_as_current_official_task():
    task, facts = inputs()
    task['source_ref']['status'] = 'historical_demo'
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    plan['need_info'] = []; plan['status'] = 'plan_ready'
    assert 'unverified_official_task_not_requested' in validate_plan(plan, task, facts)['errors']


def test_validation_detects_fact_contamination_and_unperformed_quality_scores():
    task, facts = inputs()
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    bad = deepcopy(plan)
    bad['paragraphs'][0]['claim']['grounded_facts'][0]['value'] = 'An invented achievement'
    bad['quality_diagnostics'][0]['score'] = 2
    errors = validate_plan(bad, task, facts)['errors']
    assert 'fact_value_or_evidence_changed' in errors
    assert 'unperformed_quality_evaluation_assigned_score' in errors


def test_validation_detects_broken_links_and_removed_material_requirements():
    task, facts = inputs()
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    plan['paragraphs'][1]['previous_link']['handoff_slot'] = 'responsibility'
    plan['paragraphs'][0]['required_slots'] = []
    errors = validate_plan(plan, task, facts)['errors']
    assert 'adjacent_paragraph_bridge_mismatch' in errors
    assert 'template_material_requirements_changed' in errors


def test_synthetic_profile_is_explicit_and_builder_does_not_mutate_inputs():
    task, facts = inputs()
    for fact in facts:
        fact['source']['kind'] = 'synthetic_confirmed_fact'
    before = deepcopy((task, facts))
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    assert (task, facts) == before
    assert plan['input_synthetic'] and not plan['ready_for_real_application']
    assert plan == build_paragraph_plan(task, facts, 'experience_evidence', {})


def test_unperformed_fact_check_and_free_claim_text_cannot_be_injected():
    task, facts = inputs()
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    plan['required_checks'][0]['state'] = 'pass'
    plan['paragraphs'][0]['claim']['statement_text'] = 'I won an award that is absent from the facts.'
    errors = validate_plan(plan, task, facts)['errors']
    assert 'unperformed_required_check_or_checklist_changed' in errors
    assert 'unperformed_claim_generation' in errors


def test_synthetic_demo_cannot_claim_real_application_readiness():
    task, facts = inputs()
    facts[0]['source']['kind'] = 'synthetic_confirmed_fact'
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    plan['input_synthetic'] = False
    plan['ready_for_real_application'] = True
    assert 'synthetic_or_application_readiness_misrepresented' in validate_plan(plan, task, facts)['errors']


@pytest.mark.parametrize('location', ['task', 'constraints'])
def test_partial_research_plan_exposes_declared_gaps_even_when_all_template_slots_are_filled(location):
    task, facts = inputs()
    task['constraints']['coverage_scope'] = 'past_work_section_only'
    target = task if location == 'task' else task['constraints']
    target['unfilled_task_aspects'] = ['future_research_plan', 'program_fit']
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    assert plan['status'] == 'need_info'
    assert {x['field'] for x in plan['need_info']} == {'future_research_plan', 'program_fit'}
    assert all(x['declared_unfilled_task_aspect'] for x in plan['need_info'])
    assert plan['task_coverage']['scope'] == 'past_work_section_only'
    result = validate_plan(plan, task, facts)
    assert result['valid'] and not result['ready_for_drafting']
    assert not result['full_task_coverage_confirmed']


def test_declared_missing_aspect_cannot_be_hidden_by_editing_plan_readiness():
    task, facts = inputs()
    task['constraints'].update(coverage_scope='past_work_section_only',
                               unfilled_task_aspects=['future_research_plan'])
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    plan['need_info'] = []
    plan['status'] = 'plan_ready'
    plan['task_coverage']['full_task_coverage_confirmed'] = True
    errors = validate_plan(plan, task, facts)['errors']
    assert 'declared_task_aspect_not_requested' in errors
    assert 'declared_task_coverage_changed' in errors


def test_coverage_metadata_does_not_invent_semantic_gaps():
    task, facts = inputs()
    task['constraints']['coverage_scope'] = 'past_work_section_only'
    plan = build_paragraph_plan(task, facts, 'experience_evidence', {})
    assert plan['need_info'] == []
    assert plan['task_coverage']['declared_unfilled_aspects'] == []
    assert not plan['task_coverage']['full_task_coverage_confirmed']
