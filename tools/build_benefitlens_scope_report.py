"""Render a two-page, count-bounded report from a preserved team snapshot."""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate,Paragraph,Table,TableStyle,Spacer,PageBreak
from pypdf import PdfReader

INK=colors.HexColor('#173047');TEAL=colors.HexColor('#087e8b');PALE=colors.HexColor('#eef6f7')
GRAY=colors.HexColor('#5c6c7b');W=A4[0]-88
pdfmetrics.registerFont(TTFont('ScopeKR','/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf'))
pdfmetrics.registerFont(TTFont('ScopeKRBold','/usr/share/fonts/truetype/nanum/NanumBarunGothicBold.ttf'))
STYLE={
    'title':ParagraphStyle('title',fontName='ScopeKRBold',fontSize=22,leading=29,textColor=INK,spaceAfter=10,wordWrap='CJK'),
    'head':ParagraphStyle('head',fontName='ScopeKRBold',fontSize=11,leading=16,textColor=TEAL,spaceBefore=12,spaceAfter=6,wordWrap='CJK'),
    'body':ParagraphStyle('body',fontName='ScopeKR',fontSize=10,leading=15,textColor=INK,spaceAfter=6,wordWrap='CJK'),
    'small':ParagraphStyle('small',fontName='ScopeKR',fontSize=8.7,leading=12.7,textColor=GRAY,spaceAfter=5,wordWrap='CJK'),
    'cell':ParagraphStyle('cell',fontName='ScopeKR',fontSize=9,leading=13.5,textColor=INK,wordWrap='CJK')}


def p(text,style='body'):
    return Paragraph(escape(str(text)).replace('\u00b7',',').replace('\n','<br/>'),STYLE[style])


def table(headers,rows,widths):
    t=Table([[p(v,'cell') for v in row] for row in [headers,*rows]],colWidths=widths)
    t.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),PALE),
        ('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
        ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),
        ('LINEBELOW',(0,0),(-1,-1),.35,colors.HexColor('#d8e0e6'))]))
    return t


def load_summary(snapshot,terms):
    for stage in snapshot.get('stages',[]):
        if not any(t in stage.get('name','') for t in terms):continue
        ref=stage.get('summary_ref') or stage.get('original_summary_ref')
        if not ref:continue
        path=Path(ref['path'] if isinstance(ref,dict) else ref)
        if not path.exists():continue
        raw=path.read_bytes()
        if isinstance(ref,dict) and ref.get('sha256') and sha256(raw).hexdigest()!=ref['sha256']:
            raise ValueError('Snapshot summary bytes changed')
        return json.loads(raw)
    return {}


def count(value):return '미집계' if value is None else f'{int(value):,}'


def build(snapshot_path,output):
    snapshot=json.loads(snapshot_path.read_text());output.mkdir(parents=True,exist_ok=True)
    monitor=snapshot.get('monitor_end') or snapshot.get('monitor_start') or {}
    queue=monitor.get('queue_counts') or []
    if not isinstance(queue,list) or not queue:raise ValueError('Actual queue counts are required for a report')
    by_kind=defaultdict(Counter)
    for row in queue:
        by_kind[row.get('kind','unknown')][row.get('state','unknown')]+=int(row.get('groups',row.get('count',0)))
    total=sum(sum(v.values()) for v in by_kind.values())
    interpreted=sum(v['analyzed'] for v in by_kind.values())
    from run_benefitlens_team_handoff import queue_bucket
    held=sum(n for v in by_kind.values() for key,n in v.items() if queue_bucket(key)=='unresolved')
    incomplete=total-interpreted
    state='전체 묶음 처리 중' if any(v.get(k,0) for v in by_kind.values() for k in ('pending','codex_running','waiting_codex_provider','waiting_provider_limit')) else '자동 처리 종료, 보류 확인 필요' if held else '대상 묶음 자동 처리 종료'
    state={'processing':'전체 묶음 처리 중','stopped_with_pending':'작업자 종료, 대기 그룹 남음','waiting_with_pending':'제공자 대기, 대기 그룹 남음','waiting':'제공자 대기','team_refresh_partial_failure':'후속 팀 처리 일부 실패','queue_drained_with_unresolved':'대기열 종료, 미해결 그룹 확인 필요','queue_drained_reference_review_pending':'대기열 종료, 적용 단계별 근거 확인'}.get(snapshot.get('state'),state)
    from run_benefitlens_team_handoff import completion_summary
    completion=snapshot.get('team_completion') or completion_summary(snapshot)
    teams=completion['teams'];s=teams['source_schema'];q=teams['experience_quality'];w=teams['writing'];pc=teams['personalization'];ops=teams['operations']
    at=snapshot.get('at') or datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
    try:at=datetime.fromisoformat(at.replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M KST')
    except ValueError:pass
    rows=[]
    for kind,label in [('notice','공고 표현'),('experience','경험 문맥')]:
        c=by_kind[kind];rows.append([label,count(sum(c.values())),count(c['analyzed']),count(sum(n for key,n in c.items() if queue_bucket(key)=='unresolved')),count(sum(n for key,n in c.items() if queue_bucket(key) not in ('analyzed','unresolved')))])
    story=[p('BenefitLens / '+at,'small'),p('팀별 실제 해석과 연결','title'),
        p(state,'head'),p(f'대기열 {count(total)}그룹 중 해석 상태 {count(interpreted)}그룹, 미완료 {count(incomplete)}그룹이다. 아래 직접 LLM 해석 수는 실행 영수증과 원문 근거 검증을 통과한 그룹으로 집계한다.'),
        table(['대기열 단위','전체','해석 상태','보류','대기와 실행'],rows,[104,101,101,97,W-403]),
        p('팀별로 실제 분석하고 연결한 수','head'),
        table(['팀','검증된 해석과 연결'],[
            ['원문과 스키마',f"직접 LLM 해석 {count(s['actual_llm_analyzed_notice_groups'])}공고 그룹 중 관련 {count(s['relevant_llm_analyzed_notice_groups'])}그룹. 코드 적용 {count(s['code_applied_source_occurrences'])}원문 구간. 조건과 필드의 매핑 {count(s['source_schema_mapping_instances'])}행, 한 개 이상 연결된 공고 {count(s['source_schema_linked_candidate_notices'])}건."],
            ['경험 품질',f"직접 LLM 해석 {count(q['actual_llm_analyzed_experience_groups'])}문맥 그룹 중 관련 {count(q['relevant_llm_analyzed_experience_groups'])}그룹. 문단 연결 {count(q['paragraph_linked_semantic_groups'])}그룹, 문단 및 사례 연결 {count(q['paragraph_case_linked_semantic_groups'])}그룹."],
            ['글쓰기',f"원문 연결 {count(w['source_occurrence_links'])}건, 문단 연결 관찰 {count(w['paragraph_linked_observations'])}건. 전체 문단 LLM 분석 {count(w['actual_full_paragraph_llm_analyses'])}건, 재검증 후 연결 {count(w['materialized_full_paragraph_analyses'])}건. 실제 패턴과 원문 및 사례 연결 {count(w['materialized_pattern_source_occurrence_case_links'])}건."],
            ['개인화',f"초기 입력 {count((pc.get('user_input_contract') or {}).get('initial_fields'))}항목, 질문 {count((pc.get('user_input_contract') or {}).get('question_chunks'))}묶음. 원문 검증 질문 경로 매핑  {count(pc['source_verified_question_route_links'])}건, 매핑이 있는 공고 투영 {count(pc['source_verified_notice_projections'])}건. 발행 질문 {count(pc['issued_user_questions'])}건, 개인 자격 판정 {count(pc['issued_personal_eligibility_decisions'])}건."],
            ['통합 운영',f"완료 단계 {count(ops['completed_team_stages'])}개, 실패 {count(ops['failed_team_stages'])}개, 의존성 보류 {count(ops['dependency_skipped_stages'])}개. 개인화 투영 이후 새 공고 해석 {count(ops['source_schema_projection_lag_groups'])}그룹."]],[100,W-100]),
        Spacer(1,8),p('각 팀은 보존한 단계 스냅샷의 누적값을 보고한다. 공고 그룹과 경험 그룹은 각각 한 번만 센다. 품질 팀과 작성 팀은 같은 경험 해석을 참조하므로 두 수를 더하지 않는다. 미집계는 완료 근거가 없는 항목이다.','small'),
        PageBreak(),p('문단 분석에서 승인까지','title'),
        p('작성 단계별 실제 완료','head'),
        table(['단계','수량','집계 기준'],[
            ['전체 문단 LLM 분석',count(w['actual_full_paragraph_llm_analyses']),'전체 문단을 입력하고 실행 영수증과 응답 검증을 통과한 분석'],
            ['재검증 후 분석 연결',count(w['materialized_full_paragraph_analyses']),'응답과 영수증 및 원문을 다시 검증해 연결한 분석'],
            ['실제 패턴과 원문 및 사례',count(w['materialized_pattern_source_occurrence_case_links']),'분석 패턴에서 원문 구간과 사례로 이어진 연결'],
            ['실제 기준 평가 연결',count(w['materialized_semantic_criterion_assessment_links']),'재검증한 전체 문단 분석에서 기준별 평가로 이어진 연결'],
            ['코드 기준 연결 관찰',count(w['criterion_linked_observations']),'출처가 있는 기준으로 코드 연결한 관찰'],
            ['프로그램과 회차 결합',count(w['verified_program_round_bindings']),'현재 문항 및 프로그램과 회차의 검증된 결합'],
            ['내용 승인 패턴',count(w['content_approved_patterns']),'해당 검토 단계에서 내용 승인을 받은 패턴'],
            ['프로그램별 승인 패턴',count(w['program_specific_approved_patterns']),'프로그램 범위의 승인 근거가 있는 패턴'],
            ['정식 PostgreSQL 기록',count(completion['canonical_postgresql_writes']),'후속 단계 summary에서 선언한 정식 데이터베이스 기록']],[125,62,W-187]),
        p('분석과 적용의 범위','head'),
        p('원문 연결은 동일한 출처 구간에 결과를 적용한 수다. 문단 연결은 원문 경계와 관찰의 연결 수이며, 전체 문단 LLM 분석은 별도의 실행 결과로 센다. 프로그램별 적용과 내용 승인은 각 검증 단계의 증거가 필요하다.'),
        p('개인화는 공고 근거와 같은 field_id로 공통 프로필 또는 추가 질문 경로를 연결한다. 정식 사용자 경험 사실은 사용자 프로필과 동의를 전제로 저장한다.'),
        p('후속 갱신과 남은 범위','head'),
        p('배치 또는 해석 그룹 간격에 도달하면 후속 팀 자료를 갱신한다. 대기열 종료, 제공자 대기, 오류와 작업자 종료, 새 문단 분석 결과, 코드 및 설정 변경은 마지막 후속 처리를 실행하는 계기다.'),
        p('Stage 0 완료 여부는 이 인계에서 측정하지 않았다. 실제 개인 자격 판정과 현재 접수 상태는 각 대상의 검증 근거가 있을 때 갱신한다.'),
        p('기준 시점과 근거','head'),p('이 보고서는 snapshot.json과 각 팀 summary의 보존된 파일 및 해시를 읽어 작성했다. 상단 시점 이후 들어온 결과는 다음 후속 처리에서 반영한다. 각 단계의 원래 집계 시점은 team-completion-summary.json의 evidence에 남긴다.','small')]
    pdf=output/'BenefitLens_전체범위_현황과확장_2장.pdf'
    def footer(canvas,doc):
        canvas.setFont('ScopeKR',8);canvas.setFillColor(GRAY)
        canvas.drawString(44,27,'BenefitLens | 전체 처리 범위와 팀 인계')
        canvas.drawRightString(A4[0]-44,27,str(doc.page))
    SimpleDocTemplate(str(pdf),pagesize=A4,leftMargin=44,rightMargin=44,topMargin=37,bottomMargin=42).build(story,onFirstPage=footer,onLaterPages=footer)
    reader=PdfReader(pdf);text='\n'.join(page.extract_text() for page in reader.pages)
    if len(reader.pages)!=2 or len(text)>10000 or '\u00b7' in text:raise ValueError('Report page or language contract failed')
    (output/'report.txt').write_text(text+'\n')
    result={'path':str(pdf),'pages':len(reader.pages),'characters':len(text),'state':state,'source_snapshot':str(snapshot_path),
        'sha256':sha256(pdf.read_bytes()).hexdigest()}
    (output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True);parser.add_argument('--output-root',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(build(args.snapshot,args.output_root),ensure_ascii=False))


if __name__=='__main__':main()
