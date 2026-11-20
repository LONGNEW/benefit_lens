"""Boundary tests for structure and provenance, without semantic quality labels."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_writing_architecture import (
    audit_long_matches, compile_architecture, load_contract,
    validate_architecture, write_bundle,
)


def inputs(genre='research_fit'):
    spec = load_contract()['genres'][genre]
    fields = list(spec['field_kinds'])
    task = {'task_id': 'synthetic-task', 'subject_id': 'synthetic-person',
            'experience_id': 'synthetic-experience', 'genre': genre,
            'source_ref': {'status': 'synthetic_fixture'},
            'constraints': {'max_characters': 2000}}
    facts = [{'fact_id': 'F:' + field, 'subject_id': task['subject_id'],
              'experience_id': task['experience_id'], 'field': field,
              'value': False if field == 'coursework' else field + '에 대한 합성 확인 문장입니다.',
              'kind': spec['field_kinds'][field][0], 'status': 'confirmed',
              'as_of': {'status': 'known', 'value': '2026-11-20', 'provenance': 'synthetic_fixture'},
              'provenance': 'synthetic_fixture',
              'source_ref': {'kind': 'synthetic_confirmed_fact',
                             'owner_subject_id': task['subject_id'], 'evidence_id': 'E:' + field}}
             for field in fields]
    return task, facts


@pytest.mark.parametrize('genre', ['research_fit', 'execution_proposal', 'contribution_leadership'])
def test_three_genres_produce_literal_preview_without_semantic_claim(genre):
    task, facts = inputs(genre)
    before = deepcopy((task, facts))
    result = compile_architecture(task, facts)
    assert next(iter(result)) == 'sentence_purpose_relations_ko'
    assert validate_architecture(result, task, facts)['valid']
    assert result['draft_text_preview']
    assert len(result['sections']) == 3
    assert result['semantic_validity'] == 'unknown'
    assert result['persuasiveness_effect'] == 'unmeasured'
    assert result['source_submission_identity'] == 'unknown'
    assert not result['ready_for_real_submission']
    assert (task, facts) == before


@pytest.mark.parametrize('change,reason', [
    ({'subject_id': 'another-person'}, 'different_person'),
    ({'experience_id': 'another-experience'}, 'different_experience'),
    ({'value': ''}, 'empty_evidence'),
    ({'status': 'unconfirmed'}, 'fact_not_confirmed'),
    ({'provenance': 'public_source'}, 'not_personal_confirmed_source'),
    ({'source_ref': {'kind': 'user_document', 'owner_subject_id': 'another-person', 'evidence_id': 'x'}}, 'source_owner_mismatch'),
])
def test_foreign_empty_unconfirmed_evidence_cannot_render(change, reason):
    task, facts = inputs()
    fid = facts[0]['fact_id']
    facts[0].update(change)
    result = compile_architecture(task, facts)
    used = {f for s in result['sections'] for p in s['paragraphs'] for sent in p['sentences'] for f in sent['fact_ids']}
    assert fid not in used
    assert {'fact_id': fid, 'reason': reason} in result['held_facts']
    assert result['draft_text_preview'] is None


def test_conflicts_do_not_select_one_confirmed_version():
    task, facts = inputs()
    state = next(f for f in facts if f['field'] == 'coursework')
    other = deepcopy(state); other.update(fact_id='F:contradiction', value=True)
    facts.append(other)
    task['personal_fact_bindings'] = {'coursework': ['F:coursework']}
    result = compile_architecture(task, facts)
    assert result['contradictions'][0]['field'] == 'coursework'
    assert result['draft_text_preview'] is None
    assert any(x['reason'] == 'contradictory_confirmed_values' for x in result['need_info'])


def test_declared_multiple_own_experiences_and_selected_narrative_facts():
    task, facts = inputs()
    extra = deepcopy(next(f for f in facts if f['field'] == 'past_action'))
    extra.update(fact_id='F:second-action', experience_id='second-experience', value='다른 본인 경험에서는 자료 분류를 수행했습니다.')
    facts.append(extra)
    task['allowed_experience_ids'] = [task['experience_id'], 'second-experience']
    task['selected_fact_ids'] = [f['fact_id'] for f in facts]
    result = compile_architecture(task, facts)
    assert not result['contradictions']
    assert extra['fact_id'] in result['selected_personal_fact_ids']
    assert extra['value'] in result['draft_text_preview']
    assert validate_architecture(result, task, facts)['valid']


def test_multiple_narrative_values_request_selection_instead_of_contradiction():
    task, facts = inputs()
    extra = deepcopy(next(f for f in facts if f['field'] == 'past_action'))
    extra.update(fact_id='F:second-action', value='같은 경험에서 별도의 검증을 수행했습니다.')
    facts.append(extra)
    result = compile_architecture(task, facts)
    assert not result['contradictions']
    assert any(x['reason'] == 'fact_selection_required_not_a_contradiction' for x in result['need_info'])
    assert result['draft_text_preview'] is None
    task['personal_fact_bindings'] = {'past_action': ['F:past_action', 'F:second-action']}
    assert extra['value'] in compile_architecture(task, facts)['draft_text_preview']


def test_declared_experience_allowlist_does_not_allow_another_person():
    task, facts = inputs()
    facts[0]['subject_id'] = 'different-person'
    task['allowed_experience_ids'] = [task['experience_id']]
    result = compile_architecture(task, facts)
    assert result['held_facts'][0]['reason'] == 'different_person'


def test_exclusive_state_values_in_different_experiences_need_scope_selection():
    task, facts = inputs()
    state = deepcopy(next(f for f in facts if f['field'] == 'coursework'))
    state.update(fact_id='F:later-state', experience_id='later-experience', value=True)
    facts.append(state)
    task['allowed_experience_ids'] = [task['experience_id'], 'later-experience']
    task['official_constraints'] = {'required_fact_fields': ['coursework']}
    result = compile_architecture(task, facts)
    assert not result['contradictions']
    assert any(x['field'] == 'coursework' and x['reason'] == 'fact_selection_required_not_a_contradiction' for x in result['need_info'])


def test_sentence_purposes_are_specific_to_genre_section_and_field():
    task, facts = inputs()
    result = compile_architecture(task, facts)
    purposes = {x['role_field']: x['purpose_ko'] for x in result['sentence_purpose_relations_ko']}
    assert '남아 있는 연구 질문' in purposes['research_question']
    assert '필요한 자료' in purposes['resource_need']
    assert '실제 성과와 구분' in purposes['intended_output']
    assert result['sections'][0]['paragraphs'][0]['opening_intent'] == purposes['observation']


def test_all_missing_evidence_keeps_unfilled_links_instead_of_dangling_refs():
    task, _ = inputs()
    result = compile_architecture(task, [])
    assert result['draft_text_preview'] is None
    assert result['need_info']
    assert validate_architecture(result, task, [])['valid']
    assert result['sections'][0]['paragraphs'][0]['bridge_relation'] is None


def test_false_coursework_is_preserved_and_not_used_as_eligibility():
    task, facts = inputs()
    result = compile_architecture(task, facts)
    assert result['preserved_explicit_states'][0]['value'] is False
    assert '수료하지 않았습니다' not in result['draft_text_preview']
    state = next(x for x in result['state_ledger'] if x['field'] == 'coursework')
    assert state['value'] is False and not state['selected_for_text']
    assert state['source_ref'] == next(f['source_ref'] for f in facts if f['field'] == 'coursework')
    assert 'eligibility' not in result


@pytest.mark.parametrize('selection', ['user', 'official'])
def test_optional_state_enters_text_only_when_explicitly_selected_or_relevant(selection):
    task, facts = inputs()
    if selection == 'user':
        task['personal_fact_bindings'] = {'coursework': ['F:coursework']}
    else:
        task['official_constraints'] = {'relevant_fact_fields': ['coursework']}
    result = compile_architecture(task, facts)
    assert '수료하지 않았습니다' in result['draft_text_preview']
    assert next(x for x in result['state_ledger'] if x['field'] == 'coursework')['selected_for_text']


def test_states_at_different_explicit_dates_are_not_a_contradiction():
    task, facts = inputs()
    old = next(f for f in facts if f['field'] == 'coursework')
    later = deepcopy(old); later.update(fact_id='F:later-coursework', value=True,
                                       as_of={'status': 'known', 'value': '2026-12-20', 'provenance': 'synthetic_fixture'})
    facts.append(later)
    task['personal_fact_bindings'] = {'coursework': ['F:later-coursework']}
    result = compile_architecture(task, facts)
    assert not result['contradictions']
    assert '2026-12-20 기준' in result['draft_text_preview']
    assert {s['as_of']['value'] for s in result['state_ledger']} == {'2026-11-20', '2026-12-20'}


def test_unknown_state_date_is_retained_and_asked_without_inference():
    task, facts = inputs()
    state = next(f for f in facts if f['field'] == 'coursework')
    del state['as_of']
    result = compile_architecture(task, facts)
    ledger = next(x for x in result['state_ledger'] if x['field'] == 'coursework')
    assert ledger['as_of'] == {'status': 'unknown', 'value': None}
    assert any(x['reason'] == 'state_as_of_unknown' and not x['blocks_preview'] for x in result['need_info'])
    assert result['draft_text_preview']
    task['personal_fact_bindings'] = {'coursework': ['F:coursework']}
    selected = compile_architecture(task, facts)
    assert selected['draft_text_preview'] is None
    assert all('F:coursework' not in s['fact_ids'] for section in selected['sections'] for p in section['paragraphs'] for s in p['sentences'])


def test_course_experience_is_allowed_even_when_coursework_was_not_completed():
    task, facts = inputs()
    action = next(f for f in facts if f['field'] == 'past_action')
    action['value'] = '수료하지 않은 수업에서 익힌 자료 분류 방법을 기록 정리 활동에 적용했습니다.'
    task['personal_fact_bindings'] = {'coursework': ['F:coursework']}
    result = compile_architecture(task, facts)
    assert '수업에서 익힌' in result['draft_text_preview']
    assert '수료하지 않았습니다' in result['draft_text_preview']


def test_this_research_example_places_access_check_before_document_timing_check():
    task, facts = inputs()
    next(f for f in facts if f['field'] == 'resource')['value'] = '먼저 자료실 열람 절차를 확인하겠습니다.'
    next(f for f in facts if f['field'] == 'resource_need')['value'] = '열람할 지도와 구술 기록의 작성 시점을 확인하겠습니다.'
    result = compile_architecture(task, facts)
    paragraph = next(s for s in result['sections'] if s['section_id'] == 'resource_method')['paragraphs'][0]
    assert [s['role_field'] for s in paragraph['sentences']] == ['resource', 'resource_need', 'proposed_method']
    assert result['draft_text_preview'].index('먼저 자료실') < result['draft_text_preview'].index('작성 시점을 확인')


def test_future_plan_cannot_fill_completed_result_slot():
    task, facts = inputs('contribution_leadership')
    next(f for f in facts if f['field'] == 'result')['kind'] = 'future_plan'
    result = compile_architecture(task, facts)
    assert result['draft_text_preview'] is None
    assert any(x['reason'] == 'fact_kind_conflicts_with_sentence_role' for x in result['need_info'])


def test_explicit_official_short_answer_overrides_prompt_and_style():
    task, facts = inputs()
    task['prompt_constraints'] = {'max_paragraphs': 3, 'max_characters': 10000}
    task['official_constraints'] = {'answer_format': 'single_paragraph', 'max_paragraphs': 1, 'max_characters': 2000}
    result = compile_architecture(task, facts, style_profile={'preferred_paragraph_count': 4})
    assert len(result['sections']) == 1
    assert result['preview_metrics']['paragraphs'] == 1
    assert '\n\n' not in result['draft_text_preview']
    assert result['effective_constraints']['max_characters'] == 2000


@pytest.mark.parametrize('unit,limit', [('max_characters', 15), ('max_words', 2)])
def test_explicit_limits_block_preview_without_truncation(unit, limit):
    task, facts = inputs()
    task['official_constraints'] = {unit: limit}
    result = compile_architecture(task, facts)
    assert result['draft_text_preview'] is None
    assert any(x.get('constraint') == unit for x in result['reconstruction_requests'])


def test_official_order_and_unrepresentable_order():
    task, facts = inputs()
    order = ['validation_scope', 'problem_question', 'resource_method']
    task['official_constraints'] = {'section_order': order}
    result = compile_architecture(task, facts)
    assert [s['section_id'] for s in result['sections']] == order
    task['official_constraints']['section_order'] = ['required_unmapped_heading']
    result = compile_architecture(task, facts)
    assert any(x['reason'] == 'official_order_requires_reconstruction' for x in result['reconstruction_requests'])


def test_pages_are_unknown_until_rendered_and_do_not_become_char_limit():
    task, facts = inputs()
    task['official_constraints'] = {'max_pages': 2, 'bibliography_excluded': True}
    result = compile_architecture(task, facts)
    assert any(x['constraint'] == 'max_pages' and x['actual'] is None for x in result['constraint_checks'])
    assert any(x['reason'] == 'page_limit_requires_layout_check' for x in result['need_info'])


def test_selection_question_is_not_an_eligibility_threshold():
    task, facts = inputs()
    task['selection_evidence_requests'] = [{'field': 'nvidia_relevance', 'criterion_ref': 'nv-criterion', 'question': '관련 근거를 확인해 주세요.'}]
    result = compile_architecture(task, facts)
    row = next(x for x in result['need_info'] if x['field'] == 'nvidia_relevance')
    assert row['mandatory'] is False and row['eligibility_judgment'] == 'not_performed'
    assert result['preserved_explicit_states'][0]['value'] is False


def test_official_review_mode_precedes_preview_preference():
    task, facts = inputs()
    task['draft_mode'] = 'preview'
    task['official_constraints'] = {'draft_mode': 'outline_review_only'}
    result = compile_architecture(task, facts)
    assert result['draft_mode'] == 'outline_review_only'
    assert result['draft_text_preview'] is None


def test_dangling_bridge_and_tampered_facts_are_detected():
    task, facts = inputs()
    result = compile_architecture(task, facts)
    p = result['sections'][0]['paragraphs'][0]
    p['next_paragraph_id'] = 'missing-paragraph'
    p['bridge_relation']['to_sentence_id'] = 'missing-sentence'
    p['sentences'][0]['text'] = '없는 수상 실적을 추가했습니다.'
    errors = validate_architecture(result, task, facts)['errors']
    assert 'dangling_paragraph_link' in errors
    assert 'dangling_sentence_bridge' in errors
    assert 'deterministic_contract_or_fact_trace_changed' in errors


@pytest.mark.parametrize('mode', ['review_only', 'outline_review_only'])
def test_explicit_review_only_disables_generated_narrative(mode):
    task, facts = inputs()
    task['draft_mode'] = mode
    result = compile_architecture(task, facts)
    assert result['draft_text_preview'] is None
    assert all(sent['text'] is None for s in result['sections'] for p in s['paragraphs'] for sent in p['sentences'])
    assert result['sentence_purpose_relations_ko']


def test_source_strategy_without_transfer_mapping_requests_reconstruction():
    task, facts = inputs()
    strategy = {'id': 'public-source-card', 'genre': 'research_vision',
                'selected_materials': ['OTHER:RESULT'], 'sections': [{'id': 'S1', 'order': 1, 'purpose': '관찰된 전략'}]}
    result = compile_architecture(task, facts, strategy)
    assert any(x['reason'] == 'source_strategy_transfer_mapping_missing' for x in result['reconstruction_requests'])
    assert 'OTHER:RESULT' not in result['accepted_fact_ids']


def test_long_match_audit_reports_candidates_without_plagiarism_verdict():
    source = {'source_document_id': 'D1', 'text': '일반 단어 ' + '충분히 긴 원문 문장과 일치하는 후보를 표시하는 시험 자료입니다. ' * 4}
    positive = audit_long_matches(source['text'], [source])
    negative = audit_long_matches('다른 자료로 쓴 짧은 문장입니다.', [source])
    assert positive['candidates'] and not negative['candidates']
    assert 'does_not_prove' in negative['interpretation']


def test_json_export_is_partitioned_and_each_document_under_10000(tmp_path):
    task, facts = inputs()
    result = compile_architecture(task, facts)
    manifest = write_bundle(result, tmp_path)
    assert len(manifest['pieces']) == 6
    assert all(len(p.read_text()) <= 10000 for p in tmp_path.glob('*.json'))
    assert json.loads((tmp_path / 'summary.json').read_text())['provenance'] == 'synthetic_fixture'
