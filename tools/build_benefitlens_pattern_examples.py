"""Materialize six evidence-bound interpretations from three complete originals.

Interpretations were authored by the current assistant after reading full texts.
This command validates attribution, hashes and exact spans; it does not invoke
a model or certify an effect on selection outcomes.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path

import project
from benefitlens_source_storage import read_source_bytes

SOURCES={
 'debian':('experience-dev-e847775fa63a12439bb7251e',
  'https://dev.to/linsmed/the-contribution-phase-is-over-here-is-what-i-learned-building-for-debian-2p4a'),
 'fulbright':('experience-fulbright-a4362f2b53be8b308a5e7253',
  'https://www.fulbright.or.kr/en/hye-ri-yang-3/'),
 'hymans':('experience-higherin-83060',
  'https://higherin.com/review/83060/hymans-robertson/summer-student')}

EXAMPLES=[
 {'id':'W1','kind':'writing_order','source':'debian','title':'의욕 대신 검증 가능한 주차별 계획',
  'source_observation':'작성자는 모호했던 등록 흐름 개선 계획을 코드 의존관계에 따라 다시 썼다. 앞 단계가 끝나야 다음 단계에 들어가며 매 단계에서 병합 가능한 코드를 낸다.',
  'quote':'Every phase produces working, tested, independently mergeable code.',
  'writing_order':['현재 문제와 제약','작업의 선후관계','주차별 산출물','각 산출물의 검증 방법'],
  'required_user_facts':['직접 확인한 문제','작업 간 의존관계','실행 가능 기간','데모 또는 테스트 계획'],
  'follow_up_questions':['3주차에 심사자에게 무엇을 보여줄 수 있나요?','선행 작업이 늦어지면 어떤 범위를 줄일 수 있나요?'],
  'behavior':'설계 전에 기존 구현을 읽고, 큰 개선을 독립적으로 검증할 수 있는 작업으로 나눈다.',
  'weak_example':'등록 시스템을 개선하고 많이 배우겠습니다.',
  'example':'[1~2주] 현재 흐름과 두 환경의 차이를 문서화한다. [3~4주] 리다이렉트를 분리하고 테스트로 확인한다. 이후 상태 모델과 데이터 이관을 단계별로 검증한다.',
  'example_status':'source_derived_structure; replace_scope_and_schedule_with_applicant_facts',
  'transfer_scope':'개발 제안서와 수행계획의 구성 후보. 실제 제출 전문이 아닌 작성자의 회고.',
  'selection_status':'current_round_waiting; prior_round_not_selected'},
 {'id':'W2','kind':'writing_order','source':'fulbright','title':'지원받을 이유를 지원 기관의 목적과 연결',
  'source_observation':'양혜리는 풀브라이트의 인재상과 자신의 연구가 한국과 미국 양국에 할 기여를 생각하라고 조언한다.',
  'quote':'미국과 한국 양국에 어떤 기여를 할 수 있을 지',
  'writing_order':['내가 다루는 문제','그 문제와 관련된 실제 활동','지원 기관 자원이 필요한 이유','활동 결과를 전달할 대상과 방식'],
  'required_user_facts':['연구 또는 활동 주제','지금까지 한 일','필요한 과정과 자원','결과를 활용할 사람과 전달 계획'],
  'follow_up_questions':['왜 이 지원이 필요한가요?','지원이 끝난 뒤 누구에게 무엇이 남나요?'],
  'behavior':'기관의 평가 기준을 읽고 자신의 활동과 연결되는 부분을 고른다.',
  'weak_example':'해외에서 공부하고 더 성장하고 싶습니다.',
  'example':'저는 [현장에서 확인한 문제]를 다뤄 왔습니다. [필요한 자원]을 활용해 [연구 또는 실행]을 진행하고, 결과를 [대상]에게 [방식]으로 전달하겠습니다.',
  'example_status':'illustrative_template; placeholders_require_user_facts',
  'transfer_scope':'기관 목적과 기여를 묻는 글의 구성 제안. 원문은 준비 조언이며 실제 에세이 전문이 아니다.',
  'selection_status':'author_reports_receiving_fulbright_support'},
 {'id':'W3','kind':'experience_description','source':'hymans','title':'역량 이름보다 내가 책임진 실행 과정을 제시',
  'source_observation':'후기 작성자는 보고 자동화에서 기존 업무 분석, 요구 수집, 도구 구현, 실제 양식 테스트와 진행 공유를 자신의 일로 설명한다.',
  'quote':'testing the tool across a range of real report templates',
  'writing_order':['해결할 업무 문제','내가 맡은 범위','선택하고 실행한 방법','실제 사용 조건에서의 검증','확인 가능한 결과'],
  'required_user_facts':['기존 업무의 문제','팀 전체와 구별되는 본인 역할','구현 또는 조정 내용','테스트 대상','확인된 결과'],
  'follow_up_questions':['그중 본인이 결정한 것은 무엇인가요?','성능 수치가 없다면 어떤 검증 기록을 제시할 수 있나요?'],
  'behavior':'이해관계자에게 요구를 확인하고 실제 사용 예제로 결과를 검증한다.',
  'weak_example':'Python 능력과 커뮤니케이션 능력을 키웠습니다.',
  'example':'[업무 문제]를 해결하기 위해 저는 [담당 범위]를 맡았습니다. [사용자]의 요구를 정리해 [도구]를 구현했고, [실제 양식 또는 사례]로 검증했습니다.',
  'example_status':'illustrative_template; no_unreported_efficiency_percentage',
  'transfer_scope':'활동 경험을 설명하는 구성 후보. 참여 후기를 지원 당시 제출 글로 간주하지 않는다.',
  'selection_status':'intern_participation_self_report; graduate_offer_waiting'},
 {'id':'W4','kind':'motivation','source':'hymans','title':'직업 전반의 관심을 특정 팀의 업무와 연결',
  'source_observation':'작성자는 단순히 계리사가 되고 싶다는 말보다 지원하는 사업 부문과 그 부문에 관심을 가진 이유를 조사하라고 조언한다.',
  'quote':'the specific business unit you are applying to',
  'writing_order':['지원 팀이 하는 일','그 일과 연결되는 내 경험','그 팀에서 맡고 싶은 구체적 문제'],
  'required_user_facts':['지원 팀의 실제 업무','그 업무와 연결되는 경험','지원 팀을 선택한 이유'],
  'follow_up_questions':['이 문장에서 기업명을 바꿔도 그대로 성립하나요?','지원 팀의 어떤 업무를 조사했나요?'],
  'behavior':'공고의 직함을 넘어 실제 팀과 업무 내용을 확인한다.',
  'weak_example':'유명한 회사에서 전문가로 성장하고 싶습니다.',
  'example':'[팀]의 [실제 업무]에 관심이 있습니다. [관련 경험]에서 확인한 [문제]를 바탕으로 [구체적 업무]에 기여하고 싶습니다.',
  'example_status':'illustrative_template; team_work_must_be_source_verified',
  'transfer_scope':'직무 지원동기와 면접 준비에 적용할 조언. 심사자의 공식 평가 기준은 아니다.',
  'selection_status':'intern_participation_self_report; graduate_offer_waiting'},
 {'id':'A1','kind':'observable_behavior','source':'debian','title':'피드백을 태도 형용사보다 설계 변경으로 설명',
  'source_observation':'공개 참석자 목록의 스팸 우려를 듣고, 먼저 기본 비활성화 방식의 동의를 제안해 유지관리자와 설계를 논의했다.',
  'quote':'Talk about design before you code.',
  'writing_order':['처음 생각한 방식','타인이 지적한 제약','제약을 반영한 수정','확인 또는 합의 과정'],
  'required_user_facts':['실제로 받은 피드백','처음 계획','바꾼 결정','논의나 변경을 확인할 기록'],
  'follow_up_questions':['피드백을 받은 뒤 무엇을 바꿨나요?','수정안은 누구와 어떻게 확인했나요?'],
  'behavior':'구현을 시작하기 전에 설계 쟁점을 질문하고 받은 의견을 결정에 반영한다.',
  'weak_example':'저는 열린 마음으로 피드백을 받아들입니다.',
  'example':'처음에는 [방식 A]를 생각했지만 [구체적 우려]를 듣고 [방식 B]로 수정했습니다. 구현 전 [관계자]와 [쟁점]을 확인했습니다.',
  'example_status':'illustrative_template; requires_actual_feedback_episode',
  'transfer_scope':'협업과 피드백 수용을 묻는 문항의 행동 사례 구성.',
  'selection_status':'current_round_waiting; prior_round_not_selected'},
 {'id':'A2','kind':'observable_behavior','source':'debian','title':'미선정 이후의 지속 참여를 실제 행동으로 설명',
  'source_observation':'이전 Outreachy 회차에서 선정되지 않았지만 모임 참석과 기여를 이어 갔고 다음 회차에 다시 지원했다고 썼다.',
  'quote':'I was not selected, but I stayed involved',
  'writing_order':['당시 결과','이후에도 이어 간 구체적 행동','다음 시도에서 달라진 준비'],
  'required_user_facts':['확인된 당시 결과','이후 활동과 기간','추가한 기여 또는 수정한 준비'],
  'follow_up_questions':['미선정 이후에 실제로 계속한 일은 무엇인가요?','다음 지원에서는 무엇을 다르게 준비했나요?'],
  'behavior':'결과 이후에도 회의 참여와 기여를 이어 가며 다음 시도의 근거를 만든다.',
  'weak_example':'저는 실패를 두려워하지 않고 끝까지 노력합니다.',
  'example':'[회차]에는 선정되지 않았습니다. 이후에도 [기간] 동안 [구체적 활동]을 이어 갔고, 다음 지원에서는 [새로 확인한 문제와 계획]을 반영했습니다.',
  'example_status':'illustrative_template; rejection_reason_not_inferred',
  'transfer_scope':'회복과 지속성을 묻는 문항의 후보. 이 작성자의 현재 회차 결과는 대기 중이다.',
  'selection_status':'current_round_waiting; prior_round_not_selected'}]


def validate_example(example,text):
    quote=example['quote'];start=text.find(quote)
    if start<0:
        raise ValueError('Pattern quote absent from original: '+example['id'])
    if not all(example.get(k) for k in ('source_observation','writing_order','required_user_facts',
                                      'follow_up_questions','behavior','example','transfer_scope')):
        raise ValueError('Incomplete operational pattern')
    return {'exact_quote':quote,'char_start':start,'char_end':start+len(quote),
            'line_start':text[:start].count('\n')+1,
            'line_end':text[:start+len(quote)].count('\n')+1}


def build():
    documents={}
    with project.connect() as c:
        for name,(key,url) in SOURCES.items():
            payload=c.execute('SELECT payload FROM benefitlens.experience_discovery_case WHERE case_key=%s',(key,)).fetchone()[0]
            doc=next(d for d in payload['documents'] if d['url']==url)
            raw=read_source_bytes(doc['raw']['path'],storage=doc['raw'].get('storage'))
            text_bytes=read_source_bytes(doc['text']['path'],storage=doc['text'].get('storage'))
            if sha256(raw).hexdigest()!=doc['raw']['sha256'] or sha256(text_bytes).hexdigest()!=doc['text']['sha256']:
                raise ValueError('Source hash mismatch')
            documents[name]={'case_key':key,'source_url':url,'document_id':doc['document_id'],
                'text_sha256':doc['text']['sha256'],'raw_sha256':doc['raw']['sha256'],
                'full_text_characters':len(text_bytes.decode()),'text_ref':doc['text'],
                'text':text_bytes.decode()}
    patterns=[]
    for definition in EXAMPLES:
        row=dict(definition);source_id=row.pop('source');doc=documents[source_id]
        evidence=validate_example(row,doc['text'])
        row.pop('quote')
        row.update(evidence={'source_id':source_id,**evidence},
                   interpretation_basis='assistant_review_of_complete_original',
                   validation='source_hash_and_exact_span_verified',
                   causal_success_claim=False,approved_for_automatic_drafting=False)
        patterns.append(row)
    return {'schema':'operational-writing-pattern-examples-v1',
            'model_batch_called':False,'interpretation_author':'current_conversation_assistant',
            'source_count':len(documents),'pattern_count':len(patterns),
            'sources':{key:{k:v for k,v in doc.items() if k!='text'} for key,doc in documents.items()},
            'patterns':patterns}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();result=build()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n')
    print(json.dumps({'patterns':result['pattern_count'],'originals':result['source_count'],'output':str(args.output)},ensure_ascii=False))
