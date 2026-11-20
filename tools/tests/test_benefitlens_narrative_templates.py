import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_literal_templates import render_template, render_with_questions, slot_contract
from benefitlens_narrative_templates import bridge_triples, contribution_blocks, execution_blocks, research_blocks


def test_two_tasks_keep_independent_contributions_and_connected_scope():
    paragraphs = research_blocks(('completed', 'ongoing'))
    template = {'paragraphs': paragraphs, 'slot_contract': slot_contract(paragraphs)}
    values = {
        '본인행동': {'value': '조건 구조 설계', 'provenance': 'applicant_record', 'status': 'completed'},
        '본인행동2': {'value': '새 공고 비교', 'provenance': 'applicant_record', 'status': 'ongoing'},
    }
    text = render_template(template, values)
    assert '조건 구조 설계를 수행해' in text[2]
    assert '새 공고 비교를 진행하고 있습니다' in text[3]
    assert '{{다음필요1}}' in paragraphs[2]['closing']
    assert paragraphs[3]['incoming_reference']['slot'] == '다음필요1'
    assert '{{남은질문}}' in paragraphs[3]['closing']
    assert paragraphs[4]['incoming_reference']['slot'] == '남은질문'
    assert all(x['semantic_review_status'] == 'needs_review' for x in bridge_triples(template))


def test_plan_cannot_be_rendered_as_an_accomplishment():
    paragraphs = research_blocks(('planned',))
    template = {'paragraphs': paragraphs, 'slot_contract': slot_contract(paragraphs)}
    text = render_template(template, {
        '본인행동': {'value': '조건 비교', 'provenance': 'applicant_record', 'status': 'planned'},
    })
    assert '조건 비교를 수행할 계획입니다' in text[2]
    with pytest.raises(ValueError, match='status mismatch'):
        render_template(template, {'본인행동': {
            'value': '조건 비교', 'provenance': 'applicant_record', 'status': 'completed'}})


def test_unknown_contribution_leaves_slots_and_question():
    paragraphs = research_blocks(('unknown',))
    template = {'paragraphs': paragraphs, 'slot_contract': slot_contract(paragraphs)}
    assert '{{본인행동}}' in render_template(template, {})[2]
    assert '직접 한 행동' in paragraphs[2]['missing_contribution_question']
    assert '수행했습니다' not in render_template(template, {})[2]
    result = render_with_questions(template, {})
    assert result['paragraphs'][2] is None
    assert result['questions'][0]['missing_slots'] == ['본인행동', '확인자료']


def test_successive_sections_share_a_bridge_but_require_semantic_review():
    paragraphs = research_blocks()
    for p, q in zip(paragraphs, paragraphs[1:]):
        assert p['next_opening'] == q['opening']
        assert q['incoming_reference']['slot'] in re.findall(r'\{\{(.*?)\}\}', p['closing'])
    assert len(paragraphs) == 5  # One grounded task is sufficient.
    with pytest.raises(ValueError):
        research_blocks(())


def test_every_repeated_task_declares_its_requirement_and_slots():
    for n in (1, 2, 3):
        paragraphs = research_blocks(('completed',) * n)
        contracts = slot_contract(paragraphs)
        for p in paragraphs:
            used = set(re.findall(r'\{\{(.*?)\}\}', ' '.join(p[k] for k in ('opening', 'support', 'closing'))))
            assert used <= contracts.keys()
            if 'work_status' in p:
                assert p['requirement_slot'] in used
                assert contracts[p['action_slot']]['tense'] == 'completed'


def test_public_background_requires_reference_and_cannot_fill_own_observation():
    paragraphs = research_blocks()
    template = {'paragraphs': paragraphs, 'slot_contract': slot_contract(paragraphs)}
    with pytest.raises(ValueError, match='source reference required'):
        render_template(template, {'문제맥락': {'value': '지원 준비', 'provenance': 'public_source'}})
    with pytest.raises(ValueError, match='provenance mismatch'):
        render_template(template, {'관찰결과': {'value': '공개 통계', 'provenance': 'public_source', 'source_ids': ['S1']}})
    text = render_template(template, {'문제맥락': {
        'value': '지원 준비', 'provenance': 'public_source', 'source_ids': ['S1']}})
    assert '[출처: S1]' in text[0]


def test_contribution_and_execution_do_not_invent_missing_own_evidence():
    for paragraphs, index in ((contribution_blocks(), 1), (execution_blocks(), 1)):
        template = {'paragraphs': paragraphs, 'slot_contract': slot_contract(paragraphs)}
        result = render_with_questions(template, {})
        assert result['paragraphs'][index] is None
        assert any(q['paragraph_index'] == index for q in result['questions'])


def test_ongoing_task_cannot_use_a_future_result_as_an_observation():
    paragraphs = research_blocks(('ongoing',))
    template = {'paragraphs': paragraphs, 'slot_contract': slot_contract(paragraphs)}
    with pytest.raises(ValueError, match='status mismatch'):
        render_template(template, {'확인된범위': {
            'value': '정확도 평가', 'provenance': 'applicant_record', 'status': 'planned'}})
    assert '접근했지만' not in paragraphs[2]['closing']
