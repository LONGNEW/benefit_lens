"""Reusable paragraph tactics with explicit narrative and typed applicant slots."""
from __future__ import annotations
import json
import re
from pathlib import Path
import benefitlens_end_to_end as e
from benefitlens_narrative_templates import (
    bridge_triples, contribution_blocks, execution_blocks, research_blocks,
)


def slot_contract(paragraphs):
    completed = {'사전활동', '관련작업', '구체행동', '준비근거', '개인기여범위', '증거자료', '본인책임', '확인한결과'}
    planned = {'연구목표', '계획방법', '첫단계', '다음단계', '검수방법', '향후적용행동'}
    public = {'요구되는기여', '기여대상'}
    background = {'문제맥락', '기존접근', '기존접근의한계', '원하는상태', '문제영향'}
    slots = {}
    for p in paragraphs:
        for name in re.findall(r'\{\{(.*?)\}\}', ' '.join(p[k] for k in ('opening', 'support', 'closing'))):
            tense = 'completed' if name in completed else 'planned' if name in planned else 'context'
            if name == p.get('action_slot'):
                tense = p['work_status'] if p['work_status'] != 'unknown' else 'context'
            elif p.get('work_status') in {'completed', 'ongoing'} and name.startswith(('기여결과', '확인자료', '확인된범위')):
                tense = 'completed'
            elif p.get('work_status') == 'planned' and name.startswith(('검증방법', '예상판정기준')):
                tense = 'planned'
            slots.setdefault(name, {
                'name': name, 'provenance': (['official_notice', 'applicant_record'] if name in public else
                                           ['public_source', 'applicant_record'] if name in background else ['applicant_record']),
                'tense': tense, 'required': True, 'drop_sentence_if_empty': False,
                'josa': 'automatic_hangul_particle',
            })
    return slots


def make_templates(out, task_statuses=('completed',), persist=True):
    source = out / '06-templates.json'
    if not source.exists():
        source = e.ROOT / 'pipeline/config/narrative_genres_v3.json'
    original = e.load(source)['templates']
    forms = {'research_proposal': research_blocks(task_statuses),
             'contribution_statement': contribution_blocks(),
             'execution_proposal': execution_blocks()}
    output = []
    for old in original:
        t = dict(old)
        t['template_id'] = re.sub(r'-v[0-9]+$', '', t['template_id']) + '-v3'
        t['paragraphs'] = forms[t['genre']]
        t['adaptation_note'] = '공식 문항의 순서와 분량을 우선합니다. 과제와 기여 문단은 필요한 수만큼 반복하며 완료, 진행, 계획을 구분합니다.'
        t['narrative_contract'] = {
            'level': 'paragraph_tactics', 'bridge_review': 'meaning_requires_contextual_review',
            'task_states': ['completed', 'ongoing', 'planned', 'unknown'],
            'unbound_contribution': 'ask_for_own_action_and_evidence',
        }
        t['strategy_provenance'] = {'kind': 'editorial_adaptation', 'source_document_ids': t['source_document_ids']}
        t['value_contract'] = {
            'required': ['value', 'provenance'],
            'draft_contribution_requires': ['fact_ids'],
            'public_source_requires': ['source_ids'], 'official_notice_requires': ['source_ids'],
            'work_status_required_for': ['completed', 'ongoing', 'planned'],
        }
        if t['genre'] == 'research_proposal':
            t['source_document_ids'] = ['D01', 'D03']
            t['strategy']['material_selection_rule'] = '본인의 행동과 근거가 있는 경험 중, 제시한 요구를 해결하고 다음 과제의 준비 근거가 되는 경험을 고른다. 과제 수는 보유한 근거와 공식 문항에 맞춘다.'
            t['strategy']['material_order_reason'] = '상황에서 문제의 중요성을 만들고, 요구에서 과제를 도출한다. 각 기여의 확인 범위가 남은 질문을 정하며, 기존 작업을 다음 연구에 사용할 근거로 연결한다.'
        elif t['genre'] == 'contribution_statement':
            t['strategy']['material_selection_rule'] = '문항이 묻는 기여를 본인의 판단, 행동, 확인 결과로 보여주는 경험을 고른다. 팀 결과에서 개인이 한 일을 구분할 수 있는 자료를 사용한다.'
        t['slot_contract'] = slot_contract(t['paragraphs'])
        t['required_slots'] = list(t['slot_contract'])
        t['paragraph_connections'] = bridge_triples(t)
        t['task_bindings'] = [
            {'task_index': i, 'work_status': p['work_status'],
             'requirement_slot': p['requirement_slot'], 'action_slot': p['action_slot'],
             'evidence_slots': p['evidence_slots'],
             'field_scope': 'task_specific',
             'shared_requirement': ('성공조건' if t['genre'] == 'research_proposal' else
                                    '필요한변화' if t['genre'] == 'contribution_statement' else '실행요구')}
            for i, p in enumerate((p for p in t['paragraphs'] if 'work_status' in p), 1)
        ]
        output.append(t)
    result = {'version': 'literal-templates-v3-narrative', 'templates': output}
    e.dump(out / '06-templates-v3.json', result)
    # Existing replay consumers read this compatibility path; metadata identifies v3.
    e.dump(out / '06-templates-v2.json', result)
    if persist:
        c = e.db(); c.executescript(e.SCHEMA)
        for t in output:
            c.execute('INSERT OR REPLACE INTO strategy_template VALUES(?,?,?,?,?,?)', (
                t['template_id'], t['genre'], json.dumps(t['strategy'], ensure_ascii=False),
                json.dumps(t, ensure_ascii=False), json.dumps(t['source_document_ids']),
                'narrative-v3-editorial-revision',
            ))
        c.commit(); c.close()
    lines = ['빈칸형 작성 템플릿 v3',
             '전략은 설득할 내용과 재료를 고르고, 이 전술 양식은 그 재료를 문단 사이의 논리로 연결합니다.',
             '공식 문항의 순서와 분량이 우선합니다. 아래 연구 양식은 과제 한 개의 예시이며 과제 문단을 반복할 수 있습니다.', '']
    for t in output:
        lines.extend([t['genre'], '재료 선택: ' + t['strategy']['material_selection_rule'], ''])
        for i, p in enumerate(t['paragraphs'], 1):
            lines.extend([f'문단 {i} / {p["section"]}: ' + p['purpose'],
                          p['opening'], p['support'], p['closing']])
            if p['next_opening']:
                lines.extend(['다음 첫 문장: ' + p['next_opening'], '연결의 이유: ' + p['bridge_meaning']])
            lines.append('')
    text = '\n'.join(lines)
    assert len(text) < 10000 and '\u00b7' not in text
    (out / '빈칸형_작성템플릿.txt').write_text(text)
    return result


def render_sentence(text,values):
    def replace(match):
        name,particle=match.group(1),match.group(2) or ''
        if name not in values:return match.group(0)
        value=str(values[name]['value'])
        if not value:return match.group(0)
        last=ord(value[-1]);batchim=(last-0xac00)%28 if 0xac00<=last<=0xd7a3 else None
        pairs={'을':('을','를'),'를':('을','를'),'은':('은','는'),'는':('은','는'),
          '이':('이','가'),'가':('이','가'),'과':('과','와'),'와':('과','와')}
        if particle and batchim is not None:particle=pairs[particle][0 if batchim else 1]
        refs=values[name].get('source_ids',[])
        citation=' [출처: '+', '.join(refs)+']' if values[name].get('provenance') in {'public_source','official_notice'} and refs else ''
        return value+particle+citation
    return re.sub(r'\{\{(.*?)\}\}([을를은는이가과와])?',replace,text)


def render_template(template,values):
    for name,item in values.items():
        contract=template['slot_contract'].get(name)
        if contract and item['provenance'] not in contract['provenance']:
            raise ValueError('slot provenance mismatch: '+name)
        if contract and item['provenance'] in {'public_source', 'official_notice'} and not item.get('source_ids'):
            raise ValueError('public slot source reference required: '+name)
        if contract and contract['tense'] in {'completed','ongoing','planned'} and item.get('status')!=contract['tense']:
            raise ValueError('slot work status mismatch: '+name)
    result=[]
    for p in template['paragraphs']:
        parts=[render_sentence(p[k],values) for k in ['opening','support','closing']]
        for s in p.get('optional_sentences',[]):
            if all(name in values and values[name].get('value') for name in s['requires']):
                parts.insert(2,render_sentence(s['text'],values))
        result.append(' '.join(parts))
    return result


def render_with_questions(template, values):
    paragraphs = render_template(template, values)
    questions = []
    for i, p in enumerate(template['paragraphs']):
        missing = [n for n in p.get('evidence_slots', [])
                   if not values.get(n, {}).get('value') or not values.get(n, {}).get('fact_ids')]
        if missing:
            paragraphs[i] = None
            questions.append({'paragraph_index': i, 'missing_slots': missing,
                              'question': '이 과제에서 직접 한 행동과 이를 확인할 자료는 무엇인가요?'})
    return {'paragraphs': paragraphs, 'questions': questions, 'value_bindings': values}


if __name__=='__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=e.DEFAULT_OUT)
    parser.add_argument('--task-statuses', nargs='+', default=['completed'],
                        choices=['completed', 'ongoing', 'planned', 'unknown'])
    parser.add_argument('--no-db', action='store_true')
    args = parser.parse_args()
    make_templates(args.output, tuple(args.task_statuses), persist=not args.no_db)
