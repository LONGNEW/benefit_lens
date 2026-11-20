"""Build a fresh, audited report across writing corpora and public source archives."""
from collections import Counter,defaultdict
import csv
from html import escape as esc
import json
from pathlib import Path
import re
import sqlite3

from audit_benefitlens_writing_foundations import audit,authored,page,REPO
from benefitlens_source_storage import read_source_bytes
from collect_benefitlens_writing_foundations import ROOT
from import_benefitlens_scored_writing import essay_key
from run_benefitlens_campaign import atomic_json

OUTPUT=REPO.parents[2]/'outputs/benefitlens'
SECTION=re.compile(r'(?im)^\s*(?:(?:\d+[.)]?|[IVX]+[.)])\s+)?(?:project (?:overview|description|summary)|abstract|synopsis|motivation|deliverables|timeline|implementation|prior contributions|past contributions|benefits to (?:the )?community|detailed (?:timeline|description)|expected (?:outcome|deliverables)|proposed (?:timeline|work))\s*:?\s*$',re.M)


def source_text(doc):
    return read_source_bytes(doc['text']['path'],storage=doc['text']['storage']).decode()


def main():
    records,principles,cx,base,persuade=audit(ROOT);cx.close()
    ellipse=json.loads((ROOT/'ellipse/status.json').read_text())
    revisions=json.loads((ROOT/'revisions/status.json').read_text())
    actual=sqlite3.connect('file:'+str(ROOT/'ellipse/ellipse.sqlite')+'?mode=ro',uri=True)
    if actual.execute('SELECT count(*) FROM essay').fetchone()[0]!=ellipse['unique_essays']:
        raise ValueError('ELLIPSE status differs from actual table')
    actual.close()
    buckets=defaultdict(list);unique=set();within_units=defaultdict(set)
    for d in records:
        body=source_text(d);key=essay_key(body)
        d['content_key']=key;unique.add(key);buckets[d['source_id']].append(d);within_units[d['unit']].add(key)
    proposals=[d for d in records if d['unit']=='application_proposal']
    statements=[d for d in records if d['unit']=='application_text']
    community={d['unit']:sum(x['unit']==d['unit'] for x in records) for d in records if d['unit'] in ('question','answer')}
    # The article's exact text stays linked. Section headings are observations,
    # not admissions-effect estimates or automatically approved paragraph rules.
    observations=[]
    for d in proposals:
        text=source_text(d)
        sections=[{'text':m.group().strip(),'char_start':m.start(),'char_end':m.end(),
                   'raw_match':m.group()} for m in SECTION.finditer(text)]
        if len(sections)>=3:
            observations.append({'document_id':d['document_id'],'url':d['url'],'text_ref':d['text'],
                                 'content_key':d['content_key'],'sections':sections,
                                 'interpretation':'observed_section_headings_only'})
    atomic_json(ROOT/'proposal-section-observations.json',observations)
    provenance=json.loads((ROOT/'open-guides/status.json').read_text())
    books=json.loads((ROOT/'open-books/status.json').read_text())
    summary={'at_kst':base['at_kst'],'source_records':len(records),
             'unique_exact_bodies':base['unique_source_texts'],'unique_normalized_bodies':len(unique),
             'source_counts':{sid:len(ds) for sid,ds in buckets.items()},
             'material_units':dict(Counter(d['unit'] for d in records)),
             'unique_normalized_by_unit':{k:len(v) for k,v in within_units.items()},
             'scored_complete_essays':ellipse['unique_essays_across_reliable_corpora'],
             'persuade_essays':persuade['unique_full_essays'],'ellipse_essays':ellipse['unique_essays'],
             'cross_corpus_essay_overlap':ellipse['whole_essay_overlap_with_persuade'],
             'new_scored_essays_since_round1':ellipse['unique_essays']-ellipse['whole_essay_overlap_with_persuade'],
             'new_source_records_since_round1':len(records)-6051,
             'unique_sentence_revision_pairs':revisions['unique_sentence_pairs'],
             'unique_document_revision_pairs':revisions['unique_document_pairs'],
             'revision_pairs_by_variant':revisions['unique_pairs_by_variant'],
             'revision_label_origins':revisions['annotation_origin_counts'],
             'proposal_section_observation_documents':len(observations),
             'publisher_book_discovered_chapters':books['discovered_chapters'],
             'publisher_book_collected_chapters':books['collected_chapters'],
             'source_holds':[{k:v for k,v in s.items() if k in ('id','held','held_members')} for s in provenance['sources'] if s.get('held') or s.get('held_members')],
             'semantic_pattern_batch_run':False,'canonical_experience_db_promoted':False}
    atomic_json(ROOT/'expansion-round2-summary.json',summary)
    catalogue=ROOT/'source-catalogue-round2.csv'
    with catalogue.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['document_id','source_id','unit','title','original_url','raw_path','text_path','content_key','outcome_label_unreviewed'])
        for d in records:w.writerow([d['document_id'],d['source_id'],d['unit'],d.get('title'),d['url'],d['raw']['path'],d['text']['path'],d['content_key'],d.get('source_outcome_label')])
    selected=[]
    for source in ('gsoc-iitbhu','gsoc-archive-2025','gsoc-archive-2026','academic-author-jean','graduate-admissions-siddu','academic-advice-shaily'):
        choices=[d for d in buckets[source] if d['unit'] not in ('repository_context','recommendation_letter')]
        preferred=[d for d in choices if any(o['document_id']==d['document_id'] for o in observations)]
        d=sorted(preferred or choices,key=lambda x:x.get('member',''))[0]
        pdf=ROOT/'open-guides/pdfs'/(d['raw']['sha256']+'.pdf')
        observed=next((o['sections'] for o in observations if o['document_id']==d['document_id']),[])
        selected.append({'title':d.get('member') or d.get('title'),'url':d['url'],
                         'local_pdf':str(pdf) if pdf.exists() else None,
                         'observed_headers':[s['text'] for s in observed][:8]})
    atomic_json(ROOT/'reading-examples-round2.json',selected)
    OUTPUT.mkdir(parents=True,exist_ok=True)
    report=OUTPUT/'BenefitLens_글쓰기원천_추가확장보고_20261117.txt'
    reader=OUTPUT/'BenefitLens_글쓰기원천_추가확장_20261117.html'
    checkpoints=[s for s in provenance['sources'] if 'stackexchange' in s['id']]
    quota=min(s.get('quota_remaining',300) for s in checkpoints)
    text=f'''BenefitLens 글쓰기 원천 추가 확장 보고
기준: {summary['at_kst']}

1. 확보량 변화
채점된 완성 글: 10,402편에서 {summary['scored_complete_essays']:,}편으로 증가했다.
ELLIPSE 공식 train 3,911편과 test 2,571편을 가져왔으며 PERSUADE와 같은 본문 303편을 제외했다. 순증은 6,179편이다. ELLIPSE는 종합 점수 외에 연결성, 구문, 어휘, 표현, 문법, 표기 관례 점수를 제공한다. 실제 글과 점수의 출처는 저자의 공식 저장소이며 README는 CC BY 4.0을 명시한다. 신뢰도 문제로 최종본에서 빠진 별도 원시 글은 분석 테이블에 넣지 않았다.
저장소와 커뮤니티의 원천 레코드는 6,051개에서 {len(records):,}개로 늘었다. 같은 본문을 공백과 유니코드 기준으로 정규화해 묶으면 {len(unique):,}개다. 이 수량에는 질문, 답변, 용어, 관리 문서가 포함되므로 각 유형의 수를 아래에 구분했다.

2. 새 원천과 역할
'''
    for sid in ('gsoc-iitbhu','gsoc-archive-2025','gsoc-archive-2026','academic-author-jean','graduate-admissions-siddu','academic-advice-shaily'):
        units=Counter(d['unit'] for d in buckets[sid]);text+=f'- {sid}: {dict(units)}\n'
    text+=f'''공개 제안서 파일은 {len(proposals)}개, 정규화 본문은 {len(within_units['application_proposal'])}개다. 대학원 지원 글과 기타 학술 지원 글은 {len(statements)}개, 정규화 본문은 {len(within_units['application_text'])}개다. 한 사람이 학교별로 작성한 여러 글과 독립된 사람 수는 구분한다.
Writing과 Academia 커뮤니티에서 질문 {community['question']:,}개, 답변 {community['answer']:,}개를 확보했다. Writing의 기존 6개 태그에 word-choice, non-fiction, grammar, sentence-structure, clarity, proofreading, rhetoric을 추가했다. Academia에서는 writing, statement-of-purpose, research-proposal을 조회했다. 태그 추가 시 기존 페이지를 재시작하지 않도록 고쳤으며 사이트마다 질문과 답변 대기를 분리했다.
'''
    for s in checkpoints:
        text+=f"- {s['id']}: 질문 {s.get('questions',0):,}개, 답변 {s.get('answers',0):,}개\n"
    text+=f'''
수정 전후 자료는 문장 단위의 서로 다른 쌍 {revisions['unique_sentence_pairs']:,}개와 문서 단위의 쌍 {revisions['unique_document_pairs']:,}개를 가져왔다. IteraTeR에서 사람이 수정 목적을 붙인 문장 쌍은 4,009개, 문서 쌍은 559개다. FULL 쪽의 목적은 모델 분류이며 원래 confidence를 보존한다. JFLEG에서는 원문 문장 1,501개에 대한 수정 참조 6,004행을 읽었고, 중복을 묶은 원문과 수정문의 쌍은 4,879개다. 부분 수정, 문서 수정, 작성자와 지원 경험은 서로 다른 단위다.
실제로 확인한 IteraTeR 예시에는 fluency 라벨이 붙었어도 enables를 enabling으로 바꿔 동사의 병렬 구조를 깨뜨리는 수정이 있었다. 이 라벨은 개선이 입증되었다는 뜻으로 해석하지 않는다. 작성 지침으로 승격할 자료는 문항 적합성, 사실 보존, 문법과 문맥 연결을 검토해 고른다. 이번에 전체의 품질 통과율을 측정하지 않았다.

3. 확장 방향 검토 결과
대량의 좋은 글을 확보하는 목적에는 채점 코퍼스가 유리하다. 현재의 두 코퍼스는 중고등학생의 영어 글이며 선발 합격 사례가 아니다. 문장과 문단의 질을 살펴보는 기반으로 사용한다.
지원서에 가까운 작성본을 확보하는 목적에는 공개 제안서 저장소와 저자별 지원서가 유리하다. 프로그램과 연도, 요구사항, 일정, 산출물과 이전 기여를 함께 살펴볼 수 있다. 합격과 탈락이라는 폴더명과 저장소 소개는 아직 원천의 주장으로 보관한다.
일반 글의 기준을 만드는 목적에는 공개 교재와 명시적인 채점 기준이 유리하다. 기존의 원문 검증된 기준 8개를 유지하고 새 점수와 제안서 구성을 연결해 검토한다.
문항의 해석, 재료 선택, 문장 표현을 확장하는 목적에는 질문과 답변이 유리하다. 추천 점수와 채택은 커뮤니티 반응으로 보존한다.
제안서 {len(observations)}개에서 동기, 개요, 구현, 일정, 산출물 등의 제목을 원문 위치와 함께 찾았다. 이 결과는 구조 관찰이며 문단 작성 요령의 일괄 모델 추출은 아직 실행하지 않았다.

4. 접근 확인과 보류
Writing Spaces는 대학 출판 원천에서 {books['discovered_chapters']}개 세부 글 URL을 찾았다. 목차와 robots 응답은 저장했지만 첫 PDF 요청이 HTTP 403을 반환해 PDF 수집을 보류했다. 본문 확보 수는 {books['collected_chapters']}개다. 뒤의 보류 항목은 실제 HTTP 요청을 반복한 수가 아니라 같은 호스트 보류를 적용한 수다.
Iowa State의 주석 달린 지원서 예시는 HTTP 403으로 보류했다.
devweekends 공개 제안서 모음은 메타데이터와 전체 파일 목록을 확인했다. 파일 합계 305,548,600바이트이며 이번의 전체 아카이브 읽기 한도를 넘을 수 있고 이미 수집한 모음과 중복이 있어 본문 확보 수에는 넣지 않았다. 공개 파일별 선택 수집 경로가 후속 후보로 남는다.
TU Darmstadt의 402편 논증 주석 코퍼스는 대학 메타데이터만 확인했다. 이용 계약과 새 API의 파일 연결 확인이 남아 있다.
Open Grants는 메타데이터와 전체 파일 목록을 확인했으며 저장소에 PDF 25개가 있다. 추가 수집 시 GitHub API의 남은 요청 수가 0이 되어 403을 반환했다. 응답의 재개 시각은 2026-11-17 20:22:37 KST다. 저장소 접근 거부와 구분해 보류했으며 본문 수량에는 넣지 않았다. 제공자의 한도 재설정 시각 이후 정상 요청을 재개하도록 코드를 고쳤다.
본문을 읽기 어려운 추천서 PDF는 후보로 보류했다. 추출되지 않은 자료를 확보량에 합산하지 않았다.

5. 저장과 재현
추가 원천 명세: pipeline/config/writing_foundation_sources_v3.json
기존 원천 명세를 상속하므로 문서를 합쳐 1만 자를 넘기지 않는다.
수집: /usr/bin/python3 tools/collect_benefitlens_writing_foundations.py --community-pages 3 --request-limit 80
ELLIPSE 변환: /usr/bin/python3 tools/import_benefitlens_ellipse.py
수정 전후 자료 변환: /usr/bin/python3 tools/import_benefitlens_writing_revisions.py
검증과 보고: /usr/bin/python3 tools/report_benefitlens_writing_expansion.py
자료는 공용 캐시에 원본과 추출 본문을 해시로 저장한다. 정본 경험 DB로 승격하거나 자동 반복 스케줄을 등록하지 않았다.
커뮤니티 남은 요청 수는 이번 실행의 가장 최근 관측 기준 {quota}이며, 등록한 태그의 허용된 검색 결과와 현재 질문의 답변 페이지는 모두 처리했다. 관측 수량은 답변이 하나 이상 있는 질문과 지정한 태그의 범위다.
'''
    authored(report,text)
    rows=''
    for sid,ds in buckets.items():
        label=sid
        url=ds[0]['url'].split('/blob/')[0] if '/blob/' in ds[0]['url'] else 'https://writing.stackexchange.com/' if sid=='writing-stackexchange' else ds[0]['url']
        units=Counter(d['unit'] for d in ds)
        rows+='<tr><td><a href="'+esc(url,quote=True)+'">'+esc(label)+'</a></td><td>'+esc(', '.join(k+' '+str(v) for k,v in units.items()))+'</td></tr>'
    examples=''
    for s in selected:
        link='<a href="'+esc(s['url'],quote=True)+'">공개 원문</a>'
        if s['local_pdf']:link+=' / <a href="'+esc(s['local_pdf'],quote=True)+'">확보한 PDF</a>'
        examples+='<article><h3>'+esc(s['title'])+'</h3><p>'+link+'</p>'
        if s['observed_headers']:examples+='<p>원문에서 찾은 절 제목: '+esc(' → '.join(s['observed_headers']))+'</p>'
        examples+='</article>'
    body=f'<p>{esc(summary["at_kst"])} 기준 실제 본문 확보와 중복 확인 결과입니다.</p><article><h2>채점된 완성 글 {summary["scored_complete_essays"]:,}편</h2><p>PERSUADE 10,402편 + ELLIPSE 6,482편 − 공통 본문 303편. ELLIPSE에는 연결성, 구문, 어휘, 표현, 문법, 표기 관례의 점수가 있습니다.</p><a href="https://github.com/scrosseye/ELLIPSE-Corpus">ELLIPSE 공식 배포와 평가 기준</a></article><article><h2>공개 제안서 {len(proposals)}개, 지원 글 {len(statements)}개</h2><p>제안서의 서로 다른 정규화 본문은 {len(within_units["application_proposal"])}개입니다. 프로그램별 여러 버전, 한 저자의 여러 글, 폴더의 합격 표시는 각각 출처 맥락과 함께 보관합니다.</p></article><article><h2>질문 {community["question"]:,}개와 답변 {community["answer"]:,}개</h2><p>각 답변에 질문 원문, 작성자 정보, URL과 응답에 포함된 라이선스를 연결했습니다.</p></article><h2>원천별 본문 유형</h2><table><tr><th>원천</th><th>파일과 본문 유형</th></tr>'+rows+'</table><h2>확보한 작성본 예시</h2>'+examples+'<h2>어떻게 작성 기준으로 사용할 것인가</h2><p>채점 코퍼스로 문장과 문단의 질을 보고, 공개 제안서에서 장르의 구성을 찾고, 실제 지원 문항과 확인된 개인 경험을 연결합니다. 교재의 일반 원칙, 원문에서 관찰한 구성, 지원서에 적용하는 판단을 구분합니다.</p><p>채점 글은 일반 작문 자료입니다. 공고별 후기 3배수 목표의 충족 여부는 별도로 계산합니다.</p><h2>전체 자료</h2><p><a href="'+esc(str(catalogue),quote=True)+'">전체 URL과 본문 경로 CSV</a> / <a href="'+esc(str(report),quote=True)+'">접근 조건과 후속 후보 보고서</a> / <a href="'+esc(str(ROOT/'expansion-round2-summary.json'),quote=True)+'">검증 수량 JSON</a></p>'
    revision_reader=OUTPUT/'BenefitLens_글쓰기수정_원문비교_20261117.html'
    db=sqlite3.connect('file:'+str(ROOT/'revisions/revisions.sqlite')+'?mode=ro',uri=True)
    reviewed=[('train',41,'간결성의 참고 사례','제안한이라는 표현과 중복 공백을 줄였다. 기술 명칭과 대상 모델은 유지한다.'),
              ('train',48,'표현 검토의 참고 사례','in을 into로 바꿔 정보를 모델 안에 통합한다는 동작의 방향을 표현했다.'),
              ('train',46,'문맥 검토가 필요한 사례','Furthermore와 also를 함께 넣었다. 앞 문장과의 관계와 중복 표현을 보고 적용을 결정한다.'),
              ('dev',3,'작성 지침에서 제외할 수정','enables를 enabling으로 바꾸면서 improves와 병렬인 동사 구조가 깨졌다. 원래의 fluency 라벨은 보존하고 이 수정의 적용은 제외한다.')]
    comparison=''
    decisions=[]
    for split,row,title,decision in reviewed:
        item=db.execute('''SELECT r.before_text,r.after_text,o.edit_intent,o.doc_id FROM observation o
            JOIN revision r USING(pair_key) WHERE o.variant='human_sent_level' AND o.source_split=? AND o.source_row=?''',(split,row)).fetchone()
        if not item:raise ValueError('Selected source revision missing')
        comparison+='<article><h2>'+esc(title)+'</h2><p>원 라벨: '+esc(json.loads(item[2]))+' / '+esc(split)+' '+str(row)+'행 / 문서 '+esc(item[3])+'</p><h3>수정 전</h3><blockquote>'+esc(item[0])+'</blockquote><h3>수정 후</h3><blockquote>'+esc(item[1])+'</blockquote><p><strong>이번 검토 판단:</strong> '+esc(decision)+'</p></article>'
        decisions.append({'split':split,'source_row':row,'variant':'human_sent_level','doc_id':item[3],
                          'original_intent':json.loads(item[2]),'before':item[0],'after':item[1],
                          'editorial_decision':decision,'approved_as_universal_rule':False})
    db.close();atomic_json(ROOT/'revisions/editorial-review-examples.json',decisions)
    authored(revision_reader,page('수정 전후 원문과 적용 판단','<p>원 데이터의 수정 목적과 이번 편집 판단을 구분한 예시 4개입니다. <a href="https://github.com/vipulraheja/iterater">IteraTeR 공식 원천</a>의 human_sent_level 자료를 사용했습니다.</p>'+comparison))
    body+='<h2>수정 전후와 적용 판단</h2><p><a href="'+esc(str(revision_reader),quote=True)+'">실제 수정 전후 4개와 참고, 보류, 제외 판단 보기</a></p><p>문장 수정쌍 '+format(revisions['unique_sentence_pairs'],',')+'개, 문서 단위 수정쌍 '+format(revisions['unique_document_pairs'],',')+'개를 별도로 저장했습니다. 수정 목적의 사람 주석과 모델 분류는 구분합니다.</p>'
    authored(reader,page('BenefitLens 글쓰기 원천 추가 확장',body))
    print(json.dumps({'summary':summary,'reader':str(reader),'report':str(report)},ensure_ascii=False))


if __name__=='__main__':main()
