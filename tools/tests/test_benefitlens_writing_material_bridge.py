"""Provenance and format boundaries; no semantic quality or truth scores."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benefitlens_writing_architecture import compile_architecture, fingerprint
from benefitlens_writing_material_bridge import bridge_materials, text_hash, write_bridge_bundle


def fixture_payload(two_tasks=False):
    """All personal content, identities, confirmations and dates are synthetic."""
    rows = [
        ('context', '지역 기록 정리 중 같은 장소의 이름이 시기마다 달랐습니다.', 'observation', 'completed'),
        ('problem', '명칭 차이 때문에 기록 두 건의 장소를 바로 대응하기 어려웠습니다.', 'observation', 'completed'),
        ('research_need', '두 시기의 기록을 함께 읽으려면 대응 기준이 필요했습니다.', 'observation', 'completed'),
        ('task_1.question', '첫 과제는 명칭과 위치를 함께 비교할 기준을 만드는 일이었습니다.', 'observation', 'completed'),
        ('task_1.contribution', '저는 지도 열 건의 명칭과 위치를 대조한 대응표를 만들었습니다.', 'performed_action', 'completed'),
        ('task_1.boundary', '이 대응표는 한 지역의 기록 열 건을 대상으로 했습니다.', 'observation', 'completed'),
        ('remaining_problem', '구술 기록의 작성 시점은 대응표에 반영하지 못했습니다.', 'observation', 'completed'),
        ('next_research', '다음 연구에서는 구술 기록의 작성 시점을 비교하겠습니다.', 'future_plan', 'planned'),
        ('planned_check', '위치와 시점이 불일치하는 사례를 따로 기록해 기준을 점검하겠습니다.', 'future_plan', 'planned'),
    ]
    if two_tasks:
        rows[6:6] = [
            ('task_2.question', '둘째 과제는 구술 기록의 작성 시점을 확인하는 일입니다.', 'observation', 'ongoing'),
            ('task_2.contribution', '저는 구술 기록 두 건의 작성 시점을 확인하고 있습니다.', 'performed_action', 'ongoing'),
            ('task_2.boundary', '아직 구술 기록 전체의 작성 시점을 확인하지 못했습니다.', 'observation', 'completed')]
    body = '\n'.join(r[1] for r in rows)
    source = {'experience_id': 'synthetic-exp', 'subject_id': 'synthetic-person', 'text': body,
              'provenance': 'synthetic_fixture',
              'source_ref': {'kind': 'synthetic_fixture', 'owner_subject_id': 'synthetic-person', 'evidence_id': 'synthetic-raw'}}
    payload = {'provenance': 'synthetic_fixture', 'execution_mode': 'preview',
               'task': {'task_id': 'synthetic-research', 'subject_id': 'synthetic-person',
                        'experience_id': 'synthetic-exp', 'genre': 'research_vision_iterative',
                        'research_tasks': ['task_1', 'task_2'] if two_tasks else ['task_1'],
                        'question_text': '연구 필요, 본인 기여와 다음 연구를 설명하세요.',
                        'source_ref': {'status': 'synthetic_fixture'},
                        'requirements': [{'id': 'R1', 'text': '연구 필요와 기여 및 계획', 'required': True}],
                        'constraints': {'max_characters': 2000}},
               'raw_experiences': [source], 'proposed_atoms': [], 'confirmation_events': [],
               'material_proposals': [], 'role_bindings': []}
    for i, (role, text, kind, activity) in enumerate(rows, 1):
        aid = 'A' + str(i)
        start = body.index(text)
        atom = {'atom_id': aid, 'subject_id': source['subject_id'], 'experience_id': source['experience_id'],
                'value_type': 'text', 'value': text, 'kind': kind, 'activity_status': activity,
                'source_sha256': text_hash(body), 'source_span': {'start': start, 'end': start + len(text), 'quote': text}}
        payload['proposed_atoms'].append(atom)
        payload['confirmation_events'].append({'event_id': 'E' + str(i), 'atom_id': aid,
                'confirmer_subject_id': source['subject_id'], 'decision': 'confirm',
                'atom_fingerprint': fingerprint(atom), 'confirmed_at': '2026-11-20T16:00:00+09:00'})
        payload['material_proposals'].append({'fact_id': 'F:' + aid, 'requirement_ids': ['R1'],
                                              'decision': 'include', 'reason': '합성 문항에 관한 사전 관련성 제안'})
        payload['role_bindings'].append({'role': role, 'fact_ids': ['F:' + aid], 'reason': '테스트 작성자의 명시 역할 제안'})
    return payload


def reconfirm(payload, index):
    payload['confirmation_events'][index]['atom_fingerprint'] = fingerprint(payload['proposed_atoms'][index])


def add_state(payload, value=False, value_type='boolean', **extra):
    quote = {'boolean': '정규 수업은 아직 수료하지 않았습니다.',
             'quantity': '확인한 이수 기간은 2년입니다.', 'score': '기록된 성적은 GPA 3.0입니다.'}[value_type]
    source = payload['raw_experiences'][0]
    start = len(source['text']) + 1
    source['text'] += '\n' + quote
    for index, old in enumerate(payload['proposed_atoms']):
        old['source_sha256'] = text_hash(source['text'])
        reconfirm(payload, index)
    atom = deepcopy(payload['proposed_atoms'][0])
    atom.update(atom_id='state', kind='state', activity_status='unknown', semantic_key='coursework',
                value=value, value_type=value_type, as_of={'status': 'unknown', 'value': None},
                source_span={'start': start, 'end': start + len(quote), 'quote': quote})
    atom.update(extra)
    payload['proposed_atoms'].append(atom)
    payload['confirmation_events'].append({'event_id': 'E-state', 'atom_id': 'state', 'decision': 'confirm',
              'confirmer_subject_id': 'synthetic-person', 'atom_fingerprint': fingerprint(atom),
              'confirmed_at': '2026-11-20T16:00:00+09:00'})
    return atom


def test_neutral_atoms_bind_to_one_task_and_literal_preview_with_trace():
    payload = fixture_payload()
    before = deepcopy(payload)
    out = bridge_materials(payload)
    assert payload == before
    assert out['status'] == 'preview_ready'
    assert [s['section_id'] for s in out['architecture']['sections']] == ['context_need', 'task_1', 'remaining_next']
    assert all(f['field'] == 'unassigned' for f in out['confirmed_facts'])
    assert out['compiler_facts'][0]['field'] == 'context'
    assert '완료한 기여: ' in out['draft_text_preview']
    assert 'task_2' not in json.dumps(out, ensure_ascii=False)
    assert out['architecture']['sections'][0]['paragraphs'][0]['bridge_relation']['semantic_validity'] == 'unknown'
    assert out['automatic_stages']['candidate_atoms'] == 'caller_supplied_not_extracted'
    assert all(f['confirmation_basis'] == 'input_event_not_authenticated_by_standalone_adapter' for f in out['confirmed_facts'])


def test_second_task_without_confirmed_contribution_asks_without_symmetric_fill():
    payload = fixture_payload(True)
    binding = next(r for r in payload['role_bindings'] if r['role'] == 'task_2.contribution')
    aid = binding['fact_ids'][0][2:]
    payload['confirmation_events'] = [e for e in payload['confirmation_events'] if e['atom_id'] != aid]
    out = bridge_materials(payload)
    assert out['draft_text_preview'] is None
    second = next(s for s in out['architecture']['sections'] if s['section_id'] == 'task_2')
    assert not any(s['role_field'] == 'task_2.contribution' for s in second['paragraphs'][0]['sentences'])
    assert any(q.get('field') == 'task_2.contribution' for q in out['missing_questions'])


def test_completed_and_ongoing_contributions_are_not_relabeled_as_results():
    out = bridge_materials(fixture_payload(True))
    assert out['status'] == 'preview_ready'
    assert '완료한 기여: ' in out['draft_text_preview']
    assert '진행 중인 기여: ' in out['draft_text_preview']
    sentences = [x for s in out['architecture']['sections'] for p in s['paragraphs'] for x in p['sentences']]
    assert [s['activity_status'] for s in sentences if s['role_field'].endswith('.contribution')] == ['completed', 'ongoing']


def test_future_contribution_has_explicit_plan_label():
    p = fixture_payload()
    atom = p['proposed_atoms'][4]
    new = '저는 다음 단계에서 지도 열 건의 위치를 비교하겠습니다.'
    body = p['raw_experiences'][0]['text'].replace(atom['value'], new)
    p['raw_experiences'][0]['text'] = body
    atom.update(value=new, kind='future_plan', activity_status='planned')
    for index, row in enumerate(p['proposed_atoms']):
        text = row['value']; start = body.index(text)
        row.update(source_sha256=text_hash(body), source_span={'start': start, 'end': start + len(text), 'quote': text})
        reconfirm(p, index)
    out = bridge_materials(p)
    assert out['draft_text_preview'] and '계획한 기여: ' + new in out['draft_text_preview']
    assert '완료한 기여: ' not in out['draft_text_preview']


def test_registered_public_source_cannot_be_relabelled_as_personal(monkeypatch):
    import benefitlens_writing_material_bridge as module
    config = module.load_config()
    p = fixture_payload()
    config['known_public_source_hashes'][text_hash(p['raw_experiences'][0]['text'])] = 'synthetic-public-test'
    monkeypatch.setattr(module, 'load_config', lambda: config)
    p['raw_experiences'][0].update(provenance='personal_confirmed_input')
    p['raw_experiences'][0]['source_ref']['kind'] = 'user_document'
    out = bridge_materials(p)
    assert not out['confirmed_facts']
    assert all(r['reason'] == 'public_or_unsupported_source_cannot_be_personal' for r in out['held_atoms'])


@pytest.mark.parametrize('change,reason', [
    ('no_event', 'confirmation_event_missing_ambiguous_or_unbound'),
    ('other_confirmer', 'confirmation_event_missing_ambiguous_or_unbound'),
    ('stale_event', 'confirmation_event_missing_ambiguous_or_unbound'),
    ('other_person', 'different_or_missing_person'),
    ('public', 'public_or_unsupported_source_cannot_be_personal'),
    ('bad_span', 'invalid_source_span'),
    ('bad_hash', 'source_hash_mismatch'),
    ('unanchored_value', 'text_value_must_equal_evidence_quote'),
])
def test_unconfirmed_foreign_or_unanchored_atoms_never_promote(change, reason):
    p = fixture_payload()
    if change == 'no_event': p['confirmation_events'].pop(0)
    elif change == 'other_confirmer': p['confirmation_events'][0]['confirmer_subject_id'] = 'another-person'
    elif change == 'stale_event': p['proposed_atoms'][0]['activity_status'] = 'ongoing'
    elif change == 'other_person': p['proposed_atoms'][0]['subject_id'] = 'another-person'
    elif change == 'public': p['raw_experiences'][0]['provenance'] = 'public_source'
    elif change == 'bad_span': p['proposed_atoms'][0]['source_span']['end'] -= 1
    elif change == 'bad_hash': p['proposed_atoms'][0]['source_sha256'] = 'false'
    elif change == 'unanchored_value': p['proposed_atoms'][0]['value'] = '근거에 없는 새 주장입니다.'
    out = bridge_materials(p)
    assert out['held_atoms'][0]['reason'] == reason
    assert 'F:A1' not in {f['fact_id'] for f in out['confirmed_facts']}
    assert out['draft_text_preview'] is None


def test_plan_cannot_be_promoted_to_completed_result_by_role_assignment():
    p = fixture_payload()
    p['task']['genre'] = 'contribution_leadership'
    p['role_bindings'] = [{'role': 'result', 'fact_ids': ['F:A8'], 'reason': '의도적인 잘못된 역할'}]
    out = bridge_materials(p)
    assert out['draft_text_preview'] is None
    assert any(q['reason'] == 'fact_kind_conflicts_with_sentence_role' for q in out['missing_questions'])


def test_plan_with_result_kind_is_held_before_confirmation_promotion():
    p = fixture_payload()
    p['proposed_atoms'][7]['kind'] = 'result'
    reconfirm(p, 7)
    out = bridge_materials(p)
    assert {'atom_id': 'A8', 'reason': 'activity_status_conflicts_with_fact_kind'} in out['held_atoms']


def test_literal_false_unknown_and_duration_are_distinct():
    p = fixture_payload()
    add_state(p)
    out = bridge_materials(p)
    f = next(f for f in out['confirmed_facts'] if f['atom_id'] == 'state')
    assert f['value'] is False
    assert f['as_of'] == {'status': 'unknown', 'value': None}
    assert not next(s for s in out['architecture']['state_ledger'] if s['fact_id'] == f['fact_id'])['selected_for_text']
    assert '수료하지 않았습니다' not in out['draft_text_preview']
    p['proposed_atoms'][-1]['value'] = None
    reconfirm(p, len(p['proposed_atoms']) - 1)
    assert bridge_materials(p)['held_atoms'][-1]['reason'] == 'unknown_value_requires_confirmation'
    q = fixture_payload()
    add_state(q, value=2, value_type='quantity', unit='years', semantic_key='duration')
    duration = next(f for f in bridge_materials(q)['confirmed_facts'] if f['atom_id'] == 'state')
    assert duration['typed_value']['value'] == 2 and duration['typed_value']['unit'] == 'years'
    assert duration['value'] is not True and duration['field'] != 'coursework'


def test_score_without_maximum_does_not_promote():
    p = fixture_payload()
    add_state(p, value=3.0, value_type='score', unit='GPA')
    assert bridge_materials(p)['held_atoms'][-1]['reason'] == 'score_maximum_missing_or_invalid'


@pytest.mark.parametrize('as_of', [{'status': 'known', 'value': 'next year'}, {'status': 'unknown', 'value': '2026-11-20'}])
def test_invalid_state_dates_are_held_without_guessing(as_of):
    p = fixture_payload(); add_state(p, as_of=as_of)
    assert bridge_materials(p)['held_atoms'][-1]['reason'] == 'state_as_of_invalid'


def test_short_answer_and_official_order_override_genre_and_prompt():
    p = fixture_payload()
    p['task']['prompt_constraints'] = {'section_order': ['context_need', 'task_1', 'remaining_next']}
    p['task']['official_constraints'] = {'section_order': ['task_1', 'context_need', 'remaining_next']}
    out = bridge_materials(p)
    assert out['architecture']['sections'][0]['section_id'] == 'task_1'
    p['task']['official_constraints'].update(answer_format='single_paragraph', max_paragraphs=1)
    out = bridge_materials(p)
    assert out['draft_text_preview'] and '\n\n' not in out['draft_text_preview']
    assert len(out['architecture']['sections']) == 1


def test_length_failure_preserves_facts_selection_and_exclusion_authority():
    p = fixture_payload()
    p['task']['official_constraints'] = {'max_characters': 20}
    out = bridge_materials(p)
    assert out['draft_text_preview'] is None
    assert len(out['confirmed_facts']) == len(out['material_selection']['selected_fact_ids']) == 9
    assert out['material_selection']['deletion_authority'].startswith('caller_revises')
    assert any(q['reason'] == 'explicit_limit_exceeded' for q in out['missing_questions'])


def test_synthetic_production_gate_blocks_adapter_and_direct_compiler():
    p = fixture_payload()
    p['execution_mode'] = 'production'
    out = bridge_materials(p)
    assert out['status'] == 'blocked_synthetic_production'
    assert out['confirmed_facts'] == [] and out['architecture'] is None
    preview = bridge_materials(fixture_payload())
    task = preview['compiler_task']; task['execution_mode'] = 'production'
    direct = compile_architecture(task, preview['compiler_facts'])
    assert direct['draft_text_preview'] is None
    assert direct['preview_status'] == 'blocked_synthetic_production'
    assert all(s['text'] is None for sec in direct['sections'] for p in sec['paragraphs'] for s in p['sentences'])


@pytest.mark.parametrize('marker', ['atom', 'event', 'task'])
def test_explicit_synthetic_markers_cannot_be_dropped_in_production(marker):
    p = fixture_payload(); p['execution_mode'] = 'production'
    p.pop('provenance')
    p['task']['source_ref'] = {'status': 'unverified'}
    p['raw_experiences'][0]['provenance'] = 'personal_confirmed_input'
    p['raw_experiences'][0]['source_ref']['kind'] = 'user_document'
    target = {'atom': p['proposed_atoms'][0], 'event': p['confirmation_events'][0], 'task': p['task']}[marker]
    target['provenance'] = 'synthetic_fixture'
    assert bridge_materials(p)['status'] == 'blocked_synthetic_production'


def test_unsupported_intent_returns_explicit_result():
    p = fixture_payload(); p['task']['genre'] = 'challenge_reflection'
    out = bridge_materials(p)
    assert out['status'] == 'unsupported_genre_or_structure' and out['architecture'] is None


def test_missing_explicit_research_tasks_does_not_invent_a_task():
    p = fixture_payload(); p['task']['research_tasks'] = []
    out = bridge_materials(p)
    assert out['draft_text_preview'] is None
    assert any(q['reason'] == 'explicit_research_tasks_missing' for q in out['missing_questions'])


def test_missing_declared_requirement_links_and_reused_role_are_blocked():
    p = fixture_payload()
    p['task']['requirements'].append({'id': 'R2', 'required': True, 'text': '별도 문항'})
    p['role_bindings'][1]['fact_ids'] = p['role_bindings'][0]['fact_ids']
    out = bridge_materials(p)
    assert any(q['reason'] == 'declared_required_material_missing' for q in out['missing_questions'])
    assert any(q['reason'] == 'role_fact_unselected_or_reused' for q in out['missing_questions'])
    assert out['draft_text_preview'] is None


def test_split_artifacts_have_valid_hashes_and_size(tmp_path):
    result = bridge_materials(fixture_payload(True))
    manifest = write_bridge_bundle(result, tmp_path)
    for part in manifest['pieces']:
        text = (tmp_path / part['path']).read_text()
        assert len(text) <= 10000
        assert text_hash(text) == part['sha256']
    assert all(len(p.read_text()) <= 10000 for p in tmp_path.rglob('*.json'))
