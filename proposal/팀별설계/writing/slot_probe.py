"""Read-only snapshot and fixture checks; not a generation or quality benchmark."""
import copy
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path('/home/longnew/Desktop/course_work/26-2 학기/데이터엔지니어링/benefit_lens')
collection = json.loads((REPO / '.local/experience-originals/collection.json').read_text())
source = next(d for d in collection['documents'] if d['id'] == 'nvidia-official')
raw = Path(source['text_path']).read_text()
assert hashlib.sha256(raw.encode()).hexdigest() == source['text_sha256']
sample = json.loads((REPO / 'data/sample/programs.json').read_text())
round_ = next(d for d in sample['opportunities'] if d['opportunity_id'] == 'nvidia-2027-2028')
prompt = round_['application']['prompts'][0]
assert prompt == {'kind': 'research_proposal', 'max_pages': 2, 'references_excluded': True}
assert 'The bibliography does not count toward the limit.' in raw
assert 'Connect the work to NVIDIA' in raw

facts = {
    'F1': {'experience_id': 'E1', 'accepted': True, 'kind': 'observed', 'value': 'GPU 전처리에서 배치마다 임시 버퍼를 다시 할당했다.'},
    'F2': {'experience_id': 'E1', 'accepted': True, 'kind': 'observed', 'value': '본인이 버퍼 재사용 모듈을 구현했다.'},
    'F3': {'experience_id': 'E1', 'accepted': True, 'kind': 'observed', 'value': 'NVIDIA A100, 동일 입력과 배치 1,024에서 10회 측정 중앙값 60ms에서 45ms.'},
    'F4': {'experience_id': 'E1', 'accepted': True, 'kind': 'planned', 'value': '다음에 다른 배치 크기에서도 효과가 유지되는지 확인할 계획이다.'},
    'F5': {'experience_id': 'E2', 'accepted': True, 'kind': 'observed', 'value': '별도 프로젝트의 처리량이 2배로 개선됐다.'},
    'F6': {'experience_id': 'E1', 'accepted': False, 'kind': 'observed', 'value': '모든 GPU에서 효과가 재현된다는 후보 주장.'},
}


def slot(kind, text, refs, mode='observed'):
    return {'kind': kind, 'text': text, 'fact_ids': refs, 'mode': mode, 'experience_id': 'E1'}


def arm(name, guidance, slots):
    return {'arm': name, 'guidance': guidance, 'constraints': prompt.copy(),
            'source_facts_from_reference_cases': False, 'selection_claim': False,
            'slots': slots}


arms = [
    arm('general_only', ['설계 후보: 구체적 문제와 내 행동, 비교 조건, 다음 검증을 연결'], [
        slot('vision', 'GPU 전처리에서 배치마다 되풀이되는 버퍼 할당을 줄이고자 한다.', ['F1'], 'purpose'),
        slot('progress', '제가 만든 버퍼 재사용 모듈은 A100, 동일 입력과 배치 1,024에서 지연시간 10회 중앙값을 60ms에서 45ms로 줄였다.', ['F2', 'F3']),
        slot('next_test', '다른 배치 크기에서도 이 차이가 유지되는지 검증할 계획이다.', ['F4'], 'planned'),
    ]),
    arm('case_only', ['기존 NVIDIA research-arc: 목표, 기여와 근거, 다음 단계'], [
        slot('vision', '연구 목표는 반복 GPU 전처리 비용을 낮추는 것이다. 그중 배치별 버퍼 재할당을 다룬다.', ['F1'], 'purpose'),
        slot('progress', '그 목표를 위해 제가 버퍼 재사용 모듈을 구현했다. A100의 동일 입력, 배치 1,024 조건에서 10회 지연시간 중앙값은 60ms에서 45ms로 줄었다.', ['F2', 'F3']),
        slot('next_test', '후속 연구에서는 배치 크기를 바꿔 이점을 확인할 계획이다.', ['F4'], 'planned'),
    ]),
    arm('hybrid', ['일반 근거 연결', 'research-arc 구조 후보는 같은 답을 반복해 새 지침으로 채택하지 않음'], [
        slot('vision', 'GPU 전처리의 반복 비용을 줄이는 목표에서, 배치별 버퍼 재할당에 초점을 맞췄다.', ['F1'], 'purpose'),
        slot('progress', '제가 구현한 재사용 모듈을 A100에서 동일 입력과 배치 1,024로 비교했다. 10회 지연시간 중앙값은 60ms에서 45ms로 줄었다.', ['F2', 'F3']),
        slot('next_test', '다음에는 다른 배치 크기에서 효과가 유지되는지 검증할 계획이다.', ['F4'], 'planned'),
    ]),
]


def gate(record):
    if record['constraints'] != prompt:
        return 'wrong_prompt_constraints'
    if record['source_facts_from_reference_cases'] or record['selection_claim']:
        return 'reference_fact_or_outcome_claim'
    for s in record['slots']:
        for ref in s['fact_ids']:
            f = facts[ref]
            if not f['accepted'] or f['experience_id'] != s['experience_id']:
                return 'unconfirmed_or_different_experience'
            if f['kind'] == 'planned' and s['mode'] != 'planned':
                return 'plan_as_observed_result'
    return 'accepted_fixture'


checks = {a['arm']: gate(a) for a in arms}
assert set(checks.values()) == {'accepted_fixture'}
for name, expected in [('cross_experience', 'unconfirmed_or_different_experience'),
                       ('unconfirmed', 'unconfirmed_or_different_experience'),
                       ('wrong_format_transfer', 'wrong_prompt_constraints'),
                       ('outcome_claim', 'reference_fact_or_outcome_claim'),
                       ('planned_as_observed', 'plan_as_observed_result')]:
    record = copy.deepcopy(arms[0])
    if name == 'cross_experience': record['slots'][1]['fact_ids'].append('F5')
    if name == 'unconfirmed': record['slots'][1]['fact_ids'].append('F6')
    if name == 'wrong_format_transfer': record['constraints']['references_excluded'] = False
    if name == 'outcome_claim': record['selection_claim'] = True
    if name == 'planned_as_observed': record['slots'][2]['mode'] = 'observed'
    checks[name] = gate(record)
    assert checks[name] == expected

artifact = {
    'scope': 'hand-authored comparison of three writing slot plans; all personal facts synthetic',
    'opportunity_id': round_['opportunity_id'], 'round_id': round_['round_id'],
    'notice_source': {'path': source['text_path'], 'text_sha256': source['text_sha256'],
                      'format_line': 131, 'guidance_lines': '151-163'},
    'same_inputs': 'All arms use the same cached requirement, synthetic fact set, three slots, and target-round check. Only the source of writing guidance varies.',
    'criteria_for_assessment': ['current requirement preserved', 'claims traced to accepted same-experience facts', 'helpful new coverage or question', 'unnecessary or duplicate suggestion'],
    'facts': facts, 'arms': arms, 'checks': checks,
    'manual_observation': '세 방식은 같은 문제, 구현, 관측 결과와 다음 계획을 담는다. 사례의 전환 순서는 선택지를 보태지만 이 예에서는 혼합이 새 사실이나 질문을 추가하지 않는다.',
    'case_transfer_status': 'offline example only; the underlying public texts do not verify current prompt or final submission',
    'not_tested': ['textual entailment or generator output', 'actual page layout', 'user effort or writing quality', 'acceptance outcome or causal effect'],
}
(HERE / 'slot_examples.json').write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(checks, ensure_ascii=False))
