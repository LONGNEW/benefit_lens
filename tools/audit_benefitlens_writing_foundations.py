"""Audit collected writing sources and build a local, source-linked reading pack.

No network, model, application-outcome inference or canonical DB promotion.
"""
import argparse
from collections import Counter
from datetime import datetime
import csv
from hashlib import sha256
from html import escape as esc
import json
from pathlib import Path
import sqlite3

from benefitlens_question_evidence import locate_quote, normalize_source_text
from benefitlens_source_storage import read_source_bytes
from run_benefitlens_campaign import CACHE, KST, atomic_json

REPO = Path(__file__).resolve().parents[1]
ROOT = CACHE / 'writing-foundations-v1'
STYLE = '''body{font:17px/1.7 system-ui,sans-serif;color:#172d42;background:#f5f7fa;margin:0}
main{max-width:1100px;margin:auto;padding:32px}h1{font-size:32px}h2{font-size:24px;margin-top:32px}
article,aside{background:white;padding:20px;border:1px solid #dbe2ea;border-radius:12px;margin:16px 0}
table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:12px;border:1px solid #dbe2ea}
small,.meta{color:#506579}a{color:#12639b}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}
blockquote{border-left:4px solid #46a0b8;padding-left:16px;margin:12px 0}strong{color:#123f65}'''


def page(title, body):
    return '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+'</title><style>'+STYLE+'</style><main><h1>'+esc(title)+'</h1>'+body+'</main></html>'


def authored(path, text):
    if len(text) > 10000:
        raise ValueError('Authored document exceeds 10,000 characters: '+str(path))
    if '\u00b7' in text:
        raise ValueError('Unexpected middle dot in authored document')
    path.write_text(text, encoding='utf-8')


def audit(root):
    records = json.loads((root/'open-guides/records.json').read_text())
    index = {d['document_id']:d for d in records}
    if len(index) != len(records):
        raise ValueError('Duplicate native source document ID')
    checked = set()
    for d in records:
        for field in ('raw', 'text'):
            ref = d[field]
            key = (ref['path'], ref['sha256'])
            if key not in checked:
                raw = read_source_bytes(ref['path'],storage=ref['storage'])
                if sha256(raw).hexdigest() != ref['sha256']:
                    raise ValueError('Decoded source hash mismatch: '+d['document_id'])
                checked.add(key)
        if d['unit'] == 'answer':
            parent = index[d['original_question']['document_id']]
            if parent['question_id'] != d['question_id'] or parent['text'] != d['original_question']['text']:
                raise ValueError('Answer is not bound to its original question')
    principles = json.loads((REPO/'pipeline/config/writing_principles_v1.json').read_text())
    validated = []
    for p in principles:
        d = index[p['source_id']]
        text = normalize_source_text(read_source_bytes(d['text']['path'],storage=d['text']['storage']).decode())
        loc = locate_quote(text,p['quote'])
        validated.append({**p,'document_id':d['document_id'],'url':d['url'],
                          'text_sha256':sha256(text.encode()).hexdigest(),'text_ref':d['text'],
                          'locator':loc,'interpretation':'agent_editorial_adaptation_of_explicit_source_rule'})
    atomic_json(root/'principles-validated.json',validated)
    status = json.loads((root/'persuade/status.json').read_text())
    source_archive = Path(status['source_archive'])
    if sha256(source_archive.read_bytes()).hexdigest() != status['source_archive_sha256']:
        raise ValueError('Original essay archive hash changed')
    cx = sqlite3.connect('file:'+str(root/'persuade/persuade-test.sqlite')+'?mode=ro',uri=True)
    cx.row_factory = sqlite3.Row
    texts = {}
    for e in cx.execute('SELECT text_key,full_text,text_sha256 FROM essay'):
        if sha256(e['full_text'].encode()).hexdigest()!=e['text_sha256']:
            raise ValueError('Essay body hash mismatch')
        texts[e['text_key']]=e['full_text']
    resolved=0;rated_resolved=0
    for d in cx.execute('SELECT * FROM discourse'):
        start,end=d['verified_start'],d['verified_end']
        if start is None:
            if end is not None or d['verified_text'] is not None:
                raise ValueError('Partially resolved source span')
            continue
        if texts[d['text_key']][start:end] != d['verified_text']:
            raise ValueError('Annotation does not match original essay')
        resolved+=1
        rated_resolved+=bool(d['effectiveness'])
    if len(texts)!=status['unique_full_essays'] or rated_resolved!=status['rated_elements_with_verified_span']:
        raise ValueError('Essay status differs from actual database')
    result={'at_kst':datetime.now(KST).isoformat(),'record_count':len(records),
            'unique_source_texts':len({d['text']['sha256'] for d in records}),
            'checked_original_and_processed_blob_refs':len(checked),
            'source_units':dict(Counter(d['unit'] for d in records)),
            'source_counts':dict(Counter(d['source_id'] for d in records)),
            'answer_question_bindings':sum(d['unit']=='answer' for d in records),
            'community_license_missing':sum(not d.get('content_license') for d in records if d['unit'] in ('question','answer')),
            'validated_explicit_principles':len(validated),'essay_bodies_hash_verified':len(texts),
            'exact_verified_discourse_spans':resolved,'rated_verified_spans':rated_resolved,
            'corpus_is_application_experience':False,'model_called':False,'database_promoted':False}
    atomic_json(root/'audit.json',result)
    return records,validated,cx,result,status


def export(root,out):
    records,principles,cx,result,status=audit(root)
    out.mkdir(parents=True,exist_ok=True)
    reader=out/'BenefitLens_글쓰기원천_대규모확장_20261117.html'
    sample_dir=root/'reading-examples';sample_dir.mkdir(exist_ok=True)
    # Link full source inventory rather than embedding thousands of rows in a report.
    catalogue=root/'source-catalogue.csv'
    with catalogue.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['document_id','source_id','title','unit','url','text_sha256','body_path','review_state'])
        writer.writeheader()
        for d in records:
            writer.writerow({**{k:d.get(k) for k in ('document_id','source_id','title','unit','url','review_state')},
                             'text_sha256':d['text']['sha256'],'body_path':d['text']['path']})
    sample_links=[];samples=[]
    for prompt in ['Community service','Distance learning','Car-free cities']:
        high=cx.execute('SELECT * FROM essay WHERE prompt_name=? AND holistic_score>=5 AND length(full_text) BETWEEN 2000 AND 3400 ORDER BY holistic_score DESC,text_key LIMIT 1',(prompt,)).fetchone()
        if high is None:raise ValueError('Expected a readable high score example')
        low=cx.execute('SELECT * FROM essay WHERE prompt_name=? AND holistic_score<=2 AND length(full_text) BETWEEN 1800 AND 3700 ORDER BY abs(length(full_text)-?),text_key LIMIT 1',(prompt,len(high['full_text']))).fetchone()
        if low is None:raise ValueError('Expected a same-prompt low score example')
        pair=[]
        for label,e in [('높은 점수 사례',high),('낮은 점수 사례',low)]:
            target=sample_dir/(e['native_id']+'.html')
            spans=cx.execute("SELECT discourse_type,effectiveness,verified_start,verified_end,verified_text FROM discourse WHERE text_key=? AND verified_start IS NOT NULL AND effectiveness!='' ORDER BY source_start,source_row",(e['text_key'],)).fetchall()
            roles=[]
            for d in spans:
                if d['discourse_type'] not in ('Position','Claim','Evidence','Concluding Statement'):continue
                if any(x['type']==d['discourse_type'] for x in roles):continue
                roles.append({'type':d['discourse_type'],'effectiveness':d['effectiveness'],
                              'start':d['verified_start'],'end':d['verified_end'],'quote':d['verified_text']})
            role_html=''.join('<li>'+esc(d['type']+' / '+d['effectiveness'])+' <small>원문 문자 '+str(d['start'])+'–'+str(d['end'])+'</small></li>' for d in roles)
            body='<p>'+esc(prompt)+' | 원 점수 '+str(e['holistic_score'])+'/6 | ID '+esc(e['native_id'])+'</p><p>미국 6–12학년의 논증 글입니다. 같은 문항의 다른 점수 사례를 읽기 위한 자료이며, 아래 평가는 원 데이터의 채점입니다.</p><article><h2>과제</h2><p>'+esc(e['assignment'] or '')+'</p></article><article><h2>완성 글 원문</h2><pre>'+esc(e['full_text'])+'</pre></article><article><h2>원문에 연결된 역할과 평가</h2><ul>'+role_html+'</ul></article><p>출처: <a href="https://github.com/scrosseye/persuade_corpus_2.0">PERSUADE2.0 공식 배포</a>, test 분할. 원문은 수정하지 않았습니다. 지원서 합격 여부는 제공되지 않습니다.</p>'
            authored(target,page(prompt+' — '+label,body))
            pair.append('<a href="'+esc(str(target),quote=True)+'">'+label+' '+str(e['holistic_score'])+'/6</a>')
            samples.append({'prompt':prompt,'native_id':e['native_id'],'score':e['holistic_score'],
                            'text_key':e['text_key'],'path':str(target),'verified_roles':roles})
        sample_links.append('<li>'+esc(prompt)+': '+' / '.join(pair)+'</li>')
    atomic_json(root/'reading-examples.json',samples)
    sources=[('PERSUADE2.0','https://github.com/scrosseye/persuade_corpus_2.0','완성 글 10,402편, 채점 기준 PDF 3개'),
             ('Writing Stack Exchange','https://writing.stackexchange.com/','질문 1,084개, 답변 3,809개'),
             ('Writing for Success','https://saylordotorg.github.io/text_writing-for-success/index.html','교재 세부 절 92개'),
             ('Microsoft Writing Style Guide','https://github.com/MicrosoftDocs/microsoft-style-guide','작성 지침 71개, 용어 항목 866개'),
             ('The Good Docs Project','https://gitlab.com/tgdp/templates','템플릿 28개, 해설 28개, 과정 16개, 작성 예시 4개'),
             ('Diátaxis','https://diataxis.fr/','문서 목적과 유형에 관한 파일 18개')]
    rows=''.join('<tr><td><a href="'+url+'">'+esc(name)+'</a></td><td>'+esc(count)+'</td></tr>' for name,url,count in sources)
    cards=''.join('<article><h3>'+esc(p['title'])+'</h3><p>'+esc(p['application'])+'</p><p><strong>확인:</strong> '+esc(p['check'])+'</p><small><a href="'+esc(p['url'],quote=True)+'">원문</a>, 검증된 추출 본문 '+str(p['locator']['line_start'])+'행. 지원서 적용은 편집 판단입니다.</small></article>' for p in principles)
    body='<p>2026년 11월 17일 실제 확보한 본문과 원문 연결을 확인한 글쓰기 기준입니다.</p><table><tr><th>자료</th><th>확보한 단위</th></tr>'+rows+'</table><p>저장소와 커뮤니티의 원천 레코드 6,051개에는 동일 본문 1개가 중복됩니다. 질문, 답변, 용어와 관리 문서는 서로 다른 단위로 보관합니다. 별도의 완성 글은 10,402편입니다.</p><h2>좋은 글의 기준에서 지원서로</h2><p><strong>일반 원칙 → 글의 유형 → 실제 지원 문항 → 확인된 개인 경험 → 초안과 수정</strong></p>'+cards+'<h2>한 문단의 작성 틀</h2><table><tr><th>문장 역할</th><th>작성할 내용</th></tr><tr><td>첫 문장</td><td>이 문항에서 보여 줄 기여나 판단을 제시한다.</td></tr><tr><td>뒷받침</td><td>[상황]에서 [본인이 한 행동]으로 [확인 가능한 결과]를 만들었다.</td></tr><tr><td>의미 연결</td><td>행동과 결과가 앞서 제시한 기여를 어떻게 보여 주는지 설명한다.</td></tr><tr><td>마지막 문장</td><td>보여 준 사실에서 도출되는 배움이나 다음 목표로 연결한다.</td></tr></table><p>재료는 문항의 요구, 본인의 역할, 근거의 구체성으로 고릅니다. 위 틀은 출처의 원칙을 지원서에 적용한 편집 제안이며, 수치로 검증된 합격 공식은 아닙니다.</p><h2>채점된 완성 글 원문 비교</h2><p>세 문항에서 각각 높은 점수와 낮은 점수 글을 골랐습니다. 길이가 비슷한 글을 찾았지만 점수 차이의 원인을 통제한 실험은 아닙니다.</p><ul>'+''.join(sample_links)+'</ul><h2>전체 자료</h2><p><a href="'+esc(str(catalogue),quote=True)+'">원천 6,051개 전체 URL과 본문 경로 CSV</a> / <a href="'+esc(str(root/'persuade/persuade-test.sqlite'),quote=True)+'">완성 글 10,402편과 주석 SQLite</a> / <a href="'+esc(str(root/'audit.json'),quote=True)+'">검증 결과</a></p><p>확보한 자료는 후보 단계입니다. 기술 문서의 형식, 커뮤니티의 추천, 학생 논증문의 점수는 각각 맥락을 유지하며 검토합니다. 프로그램별 후기 3배수 충족 여부는 별도로 계산해야 합니다.</p>'
    authored(reader,page('BenefitLens 글쓰기 원천과 적용 기준',body))
    report=out/'BenefitLens_글쓰기원천_확장보고_20261117.txt'
    text='''BenefitLens 글쓰기 원천 확장 보고
기준: '''+result['at_kst']+'''

1. 이번에 바꾼 수집 방향
기존의 프로그램별 합격 후기와 지원서에서 일반 글쓰기 교재, 공개 작성 템플릿, 채점된 실제 글, 커뮤니티의 문항과 답변으로 확장했다. 공개 API의 본문과 버전이 고정된 저장소 아카이브를 Python으로 가져왔다.

2. 실제 확보
- PERSUADE2.0: 중복 제거한 완성 글 10,402편, 과제 15개, 원 채점 1~6점. 원본 CSV 112,117행은 글의 부분 주석 행이다. 효과 평가가 있는 부분은 93,677개이며 이 중 93,551개가 실제 원문의 문자 위치와 연결된다. 미국 6~12학년 논증 글이다.
- Writing Stack Exchange: 질문 1,084개와 답변 3,809개. 각 답변에 질문 원문, 작성자, URL, 라이선스, 커뮤니티 점수를 연결했다. 이미지뿐인 본문 2개는 보류했다.
- Writing for Success: 세부 절 92개. 전체 장과 하위 절을 이중 집계하지 않았다. 주제문, 근거, 문단 연결, 도입과 결론, 수정 사례를 포함한다.
- Microsoft 공식 스타일 저장소: 작성 지침 71개와 용어 항목 866개. 보관된 공식 저장소의 시점 고정본이다.
- The Good Docs Project 현재 GitLab: 작성 템플릿 28개, 해설 28개, 과정 16개, 실제 작성 예시 4개, 자료 목록 16개, 스타일 지침 1개, 저장소 관리 문서 14개. 관리 문서는 작성 템플릿 집계에서 제외했다.
- Diátaxis: 목적과 독자에 따른 문서 유형 지침 18개.
- PERSUADE: 채점 기준 PDF 3개와 배포 설명 1개.
저장소와 커뮤니티 원천 레코드 6,051개, 서로 다른 본문 6,050개다. 완성 글 10,402편은 별도 테이블이다.

3. 좋은 글의 기준과 지원서 적용
교재와 공식 지침의 원문에서 8개 기준을 골라 정확한 인용 위치를 검증했다: 독자와 목적, 주제문, 문단 통일성, 구체적인 근거, 문장과 문단 연결, 근거에 맞는 결론, 간결성, 용어 일관성.
재료 선택: 문항의 요구와 연결되는 경험 중 본인의 역할과 확인 가능한 결과가 있는 사례를 고른다.
문단 첫 문장: 보여 줄 기여나 판단을 제시한다.
중간 문장: 상황, 본인의 행동, 결과를 쓰고 그 근거가 요점을 뒷받침하는 이유를 설명한다.
문단 마지막 문장: 해당 사례에서 도출할 수 있는 배움이나 다음 목표로 닫는다.
다음 문단: 앞 사례가 남긴 질문이나 다음으로 확인할 역량에 연결한다.
일반 글쓰기 원칙의 지원서 적용은 이번 편집 판단이다. 실제 공고의 문항, 글자 수와 본인의 사실을 확인해 형식을 조정한다.
새 분석 명세 operational_writing_pattern_prompt_v4.txt는 원문 관찰, 원문에 명시된 원칙, 유형별 형식, 지원서 적용 판단을 구분한다. 이번 작업에서 일괄 LLM 패턴 추출은 실행하지 않았다.

4. 계속 확장할 수 있는 경로
Writing Stack Exchange: 공개 search/advanced API로 태그를 각각 조회하고 질문 ID 묶음으로 답변을 페이지 단위로 받는다. 6개 검색 모두 다음 질문 페이지는 3이며 현재 질문의 답변 페이지 처리는 끝났다. 일일 남은 요청 수는 마지막 응답 기준 202다. backoff와 HTTP 제한은 재시도 보류 상태로 저장한다.
GitHub와 GitLab: 공개 메타데이터에서 커밋을 고정한 뒤 아카이브 전체에서 등록한 문서 형식을 읽는다. 원본, 본문, 라이선스 파일과 해시를 보관한다. 저장소 코드는 실행하지 않는다.
PERSUADE: 저자의 공개 Drive 배포에서 test ZIP 14,060,214바이트를 가져왔다. ZIP 암호는 저자 README에 공개되어 있다. train CSV 616,894,963바이트는 Drive 연결 도구의 256MiB 파일 한도를 넘어 이번에는 확보하지 못했다.
DREsS: 저자 사이트에 신규 실제 글 2,279편, 기존 표준화 글 6,515편, 합성 글 40,185편이 안내되어 있다. 동의 양식 제출이 필요해 수집하지 않았다.
추가 탐색 씨앗: Google Technical Writing, Google developer style, UNC Writing Center, Write the Docs. 본문 수집량에는 포함하지 않았다.

5. 검증과 범위
모든 원천 레코드의 원본과 전처리 본문 해시, 답변과 질문 연결, 완성 글 본문 해시, 검증된 부분의 실제 원문 일치를 검사했다. 정확한 원문 연결이 안 된 평가 부분 126개는 원 인용용에서 제외한다. 변환 코드는 저장소 관리 문서, 용어 항목, 템플릿을 구분한다. 커뮤니티 질문 98개는 API 응답에 개별 라이선스 값이 없으며, 재배포 검토 대상으로 표시했다.
원천 자료는 후보 상태이며 정본 경험 DB로 승격하지 않았다. 일반 글과 채점은 프로그램별 합격 경험과 다르므로, 이번 수량으로 공고 대비 후기 3배수 달성을 주장할 수 없다.
수집은 이번 실행을 마쳤으며 상시 백그라운드 서비스는 등록하지 않았다.

6. 파일과 재실행
원천 명세: pipeline/config/writing_foundation_sources_v1.json
기준 명세: pipeline/config/writing_principles_v1.json
수집: /usr/bin/python3 tools/collect_benefitlens_writing_foundations.py --community-pages 2 --request-limit 64
검증과 읽기 자료: /usr/bin/python3 tools/audit_benefitlens_writing_foundations.py
수집은 중간 페이지를 이어서 진행하며 읽은 응답은 캐시를 재사용한다. 새 수집은 API가 허용하는 요청 한도 안에서 진행한다.
이번 변경의 표적 테스트 11개가 통과했다. 읽기 자료 7개의 HTML 구조, 로컬 링크와 1만 자 제한을 검사했다.
'''
    authored(report,text)
    cx.close()
    return {'audit':result,'reader':str(reader),'report':str(report)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,default=REPO.parents[2]/'outputs/benefitlens')
    args=parser.parse_args()
    args.root.resolve().relative_to(CACHE.resolve())
    print(json.dumps(export(args.root,args.output),ensure_ascii=False))
