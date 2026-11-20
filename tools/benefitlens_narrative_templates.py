"""Editorial paragraph tactics: a reason for each task and each transition.

These forms implement a requested narrative, not an observed success rule.
The official question determines which sections are used and their length.
"""
from __future__ import annotations


def block(purpose, opening, support, closing, relation, sources, section, **extra):
    return {
        'purpose': purpose, 'opening': opening, 'support': support, 'closing': closing,
        'bridge_meaning': relation, 'next_opening': '', 'optional_sentences': [],
        'section': section, 'provenance': {
            'kind': 'editorial_adaptation', 'informed_by': sources,
            'claim': '요청한 서사에 맞춘 편집 양식이며 선정 효과를 검증한 규칙은 아닙니다.',
        }, **extra,
    }


def research_blocks(task_statuses=('completed',)):
    """Repeat task/contribution/scope without inventing another achievement."""
    allowed = {'completed', 'ongoing', 'planned', 'unknown'}
    if not task_statuses or any(s not in allowed for s in task_statuses):
        raise ValueError('At least one task with an explicit work status is required')
    paragraphs = [
        block('어떤 상황에서 왜 이 문제가 중요한지 독자가 이해하게 한다.',
            '{{문제맥락}}에서는 {{원하는상태}}가 중요합니다.',
            '그러나 {{기존접근}}만으로는 {{해결하려는문제}}가 남습니다. 저는 {{사전활동}}을 통해 {{관찰결과}}를 관찰했습니다.',
            '이 간극을 줄이려면 {{해결요구}}가 필요합니다.',
            '문제가 만드는 간극에서 다음 문단의 해결 요건을 도출한다.', ['D01'], '상황과 문제'),
        block('문제를 해결했다고 말할 기준과 먼저 풀 과제를 정한다.',
            '이 요구가 충족되었다고 판단하려면 {{성공조건}}을 만족해야 합니다.',
            '여기서 {{핵심장애}} 때문에 {{기존접근의한계}}가 생깁니다.',
            '따라서 먼저 다룰 과제는 {{핵심과제}}입니다.',
            '해결 요건을 가로막는 장애가 첫 과제를 선택한 이유가 된다.', ['D01'], '요구와 과제',
            incoming_reference={'slot': '해결요구', 'phrase': '이 요구'}),
    ]
    for i, status in enumerate(task_statuses):
        suffix = '' if i == 0 else str(i + 1)
        entry = '핵심과제' if i == 0 else '다음필요' + str(i)
        exit_slot = '남은질문' if i == len(task_statuses) - 1 else '다음필요' + str(i + 1)
        slot = lambda name: '{{' + name + suffix + '}}'
        action = slot('본인행동')
        result = slot('기여결과')
        if status == 'completed':
            evidence = f'저는 {action}을 수행해 {result}를 얻었습니다. {slot("확인자료")}가 보여주는 범위는 {slot("확인된범위")}입니다.'
        elif status == 'ongoing':
            evidence = f'현재 저는 {action}을 진행하고 있습니다. 지금까지의 {slot("확인자료")}에서는 {slot("확인된범위")}까지 확인했습니다.'
        elif status == 'planned':
            evidence = f'이를 위해 {action}을 수행할 계획입니다. {slot("검증방법")}으로 {slot("예상판정기준")}을 확인하겠습니다.'
        else:
            evidence = '이 과제에서 본인이 한 일은 ' + slot('본인행동') + '이며, 이를 뒷받침하는 자료는 ' + slot('확인자료') + '입니다.'
        reference = '이 과제' if i == 0 else '이 남은 과제'
        opening = reference + '에서 충족할 요구는 ' + slot('충족할요구') + '입니다. 이를 위해 ' + slot('선택한접근') + '을 택한 이유는 ' + slot('접근선택이유') + '입니다.'
        closing = slot('기여의의') + '라는 점에서 앞선 요구에 접근했지만, 다음으로 풀어야 할 것은 {{' + exit_slot + '}}입니다.'
        if status in {'planned', 'unknown'}:
            opening = reference + '에서 충족할 요구는 ' + slot('충족할요구') + '입니다. 검토할 접근은 ' + slot('선택한접근') + '이며 그 이유는 ' + slot('접근선택이유') + '입니다.'
            closing = '이 접근이 ' + slot('기여의의') + '로 이어지는지 살펴보고, 남은 {{' + exit_slot + '}}도 확인해야 합니다.'
        elif status == 'ongoing':
            closing = '아직 확인이 필요한 부분은 {{' + exit_slot + '}}입니다.'
        paragraphs.append(block(
            '이 과제를 고른 이유, 본인 기여, 확인 범위를 한 문단에 묶는다.',
            opening,
            evidence,
            closing,
            '기여가 해결한 범위와 남겨 둔 범위를 구분해 다음 과제의 필요를 만든다.',
            ['D01'], '과제와 본인 기여', work_status=status,
            action_slot='본인행동' + suffix,
            requirement_slot='충족할요구' + suffix,
            evidence_slots=['본인행동' + suffix, ('검증방법' if status == 'planned' else '확인자료') + suffix],
            incoming_reference={'slot': entry, 'phrase': reference},
            missing_contribution_question=(
                '이 과제에서 직접 한 행동, 산출물, 확인 근거는 무엇인가요?' if status == 'unknown' else ''),
        ))
    paragraphs.extend([
        block('남은 문제와 지금까지의 작업이 다음 연구에 주는 기반을 연결한다.',
            '이 질문이 남은 이유는 {{미해결이유}}입니다.',
            '앞선 작업에서 마련한 {{관련작업}}은 {{다음연구에쓰는방식}}에 활용할 수 있습니다.',
            '이를 바탕으로 다음 연구에서 확인할 질문을 {{연구질문}}으로 좁혔습니다.',
            '기존 기여를 후속 질문을 실제로 다룰 수 있는 준비 근거로 사용한다.', ['D01'], '남은 문제와 준비',
            incoming_reference={'slot': '남은질문', 'phrase': '이 질문'}),
        block('후속 질문을 실행과 판정으로 연결하고 처음 문제에 대한 의미로 닫는다.',
            '이 질문에 답하기 위해 {{계획방법}}을 진행하겠습니다.',
            '{{비교조건}}에서 {{평가기준}}을 살펴보고 {{판정기준}}에 따라 질문의 답을 판단하겠습니다.',
            '이 결과는 처음 제시한 {{해결하려는문제}}에 대해 {{남길결과물}}을 제공하고, {{기여대상}}의 {{기대되는변화}}를 판단할 근거가 됩니다.',
            '질문의 답이 처음 문제에 어떤 판단이나 변화를 가능하게 하는지 설명한다.', ['D01', 'D03'], '다음 연구와 의의',
            incoming_reference={'slot': '연구질문', 'phrase': '이 질문'}),
    ])
    return connect(paragraphs)


def contribution_blocks():
    return connect([
        block('변화가 필요했던 상황과 그 안에서 본인이 맡은 역할을 제시한다.',
            '{{경험상황}}에서 {{영향받는사람}}은 {{겪던어려움}}을 겪고 있었습니다.',
            '제가 맡은 {{본인책임}}에서 그 원인을 살펴보니 {{문제원인}}이 중요했습니다.',
            '그래서 제가 해결하려 한 것은 {{필요한변화}}였습니다.',
            '어려움의 원인이 대응 선택의 이유가 된다.', ['D02'], '상황과 책임'),
        block('대응을 고른 판단과 개인 행동의 차이를 보여준다.',
            '이 변화를 위해 {{선택한대응}}을 택했습니다.',
            '{{판단근거}} 때문에 이 대응이 적절하다고 보았고, 저는 {{구체행동}}을 수행했습니다.',
            '그 결과로 관찰한 변화는 {{확인한결과}}입니다.',
            '본인 행동으로 생긴 변화의 범위와 의미를 다음 문단에서 해석한다.', ['D02'], '판단과 행동',
            incoming_reference={'slot': '필요한변화', 'phrase': '이 변화'},
            work_status='completed', action_slot='구체행동', requirement_slot='필요한변화',
            evidence_slots=['구체행동', '증거자료']),
        block('결과의 범위를 밝히고 이후에도 사용할 판단 원리를 설명한다.',
            '이 변화는 {{증거자료}}에서 확인할 수 있습니다.',
            '이 결과에서 제가 기여한 부분은 {{개인기여범위}}이며, 남아 있는 한계는 {{결과의한계}}입니다.',
            '이 경험에서 이후에도 적용할 수 있는 판단은 {{배운원리}}입니다.',
            '경험의 결과를 다른 상황에도 사용할 수 있는 판단으로 바꾼다.', ['D02'], '결과와 해석',
            incoming_reference={'slot': '확인한결과', 'phrase': '이 변화'},
            evidence_slots=['증거자료', '개인기여범위']),
        block('그 판단이 지원 기회에서 요구하는 기여에 어떻게 쓰이는지 제시한다.',
            '이 판단은 이 기회가 요구하는 {{요구되는기여}}에 적용할 수 있습니다.',
            '{{경험과요구의연결근거}}를 바탕으로 {{향후적용행동}}을 제안합니다.',
            '이때 확인할 변화는 {{기여확인방법}}이며, 이것이 제가 이 기회에 보탤 수 있는 {{근거가있는역량}}의 구체적인 쓰임입니다.',
            '상대가 원하는 기여를 본인 경험으로 수행할 경로를 보여준다.', ['D02'], '지원 기회와 연결',
            incoming_reference={'slot': '배운원리', 'phrase': '이 판단'}),
    ])


def execution_blocks():
    return connect([
        block('누가 어떤 문제를 겪는지에서 제안의 필요를 만든다.',
            '{{대상상황}}에서 {{문제영향}} 때문에 {{실행문제}}를 해결해야 합니다.',
            '{{현황자료}}가 보여주는 {{관찰한현상}}을 고려하면 {{기존방식의한계}}가 남습니다.',
            '따라서 이 제안이 우선 충족해야 할 요구는 {{실행요구}}입니다.',
            '문제의 영향과 기존 한계에서 결과물의 요구를 도출한다.', ['D03'], '필요와 요구'),
        block('해결 요건에서 최소 결과물과 선택 이유를 제시한다.',
            '이 요구에 대응해 먼저 만들 결과물은 {{최소산출물}}입니다.',
            '{{준비근거}}를 활용할 수 있고 {{접근선택이유}} 때문에 이 범위를 먼저 택했습니다.',
            '이 결과물이 마련되어야 {{후속작업}}을 진행할 수 있습니다.',
            '첫 결과물이 후속 작업을 가능하게 하는 구체적인 의존 관계를 만든다.', ['D03'], '범위와 접근',
            incoming_reference={'slot': '실행요구', 'phrase': '이 요구'},
            evidence_slots=['준비근거']),
        block('작업의 순서와 단계 사이에 넘길 결과를 설명한다.',
            '이 후속 작업을 위해 먼저 {{첫단계}}를 진행하겠습니다.',
            '여기서 얻은 {{단계간인계물}}을 사용해 {{다음단계}}를 수행하겠습니다.',
            '이 진행을 계속할지 판단할 기준은 {{완료기준}}입니다.',
            '작업이 끝났다는 주장을 마지막 문단의 확인 방법에 연결한다.', ['D03'], '실행과 연결',
            incoming_reference={'slot': '후속작업', 'phrase': '이 후속 작업'},
            work_status='planned', action_slot='첫단계', requirement_slot='실행요구',
            evidence_slots=['첫단계']),
        block('검수와 잔여 범위를 밝히고 처음 문제의 해결로 되돌아간다.',
            '이 기준은 {{실행조건}}에서 {{검수방법}}으로 확인하겠습니다.',
            '{{검토상대}}와 결과를 검토해 {{최종인계물}}을 전달하고, 남은 {{잔여범위}}는 후속 과제로 남기겠습니다.',
            '이 결과가 {{실행문제}}를 얼마나 줄였는지는 {{문제해결확인방법}}으로 판단하겠습니다.',
            '완료의 기준을 처음 제안한 문제의 개선 여부에 대응시킨다.', ['D03'], '검수와 문제 해결',
            incoming_reference={'slot': '완료기준', 'phrase': '이 기준'}),
    ])


def connect(paragraphs):
    for i, paragraph in enumerate(paragraphs):
        paragraph['next_opening'] = paragraphs[i + 1]['opening'] if i + 1 < len(paragraphs) else ''
    return paragraphs


def bridge_triples(template):
    return [
        {'from_section': p['section'], 'to_section': q['section'],
         'last_sentence': p['closing'], 'next_first_sentence': q['opening'],
         'reference': q.get('incoming_reference'),
         'relationship': p['bridge_meaning'], 'semantic_review_status': 'needs_review'}
        for p, q in zip(template['paragraphs'], template['paragraphs'][1:])
    ]
