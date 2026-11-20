"""Render the execution report with literal templates and separate flow figures."""
from pathlib import Path
import json
import re
import sys
from xml.sax.saxutils import escape
sys.path[:0]=['/home/longnew/.cache/codex-pdf-tools/reportlab','/home/longnew/.cache/codex-pdf-tools/pymupdf']
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor,white
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Paragraph,Table,TableStyle
from reportlab.lib.styles import ParagraphStyle
import pymupdf
import benefitlens_end_to_end as e

OUT=Path(sys.argv[1]) if len(sys.argv)>1 else e.DEFAULT_OUT
PW,PH=A4;M=48;W=PW-2*M
INK=HexColor('#182c38');BLUE=HexColor('#246777');PALE=HexColor('#eff5f6');GRAY=HexColor('#586b76')
pdfmetrics.registerFont(TTFont('N','/usr/share/fonts/truetype/nanum/NanumGothic.ttf'))
pdfmetrics.registerFont(TTFont('B','/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf'))
pdfmetrics.registerFontFamily('N',normal='N',bold='B',italic='N',boldItalic='B')
STYLES={k:ParagraphStyle(k,fontName='B' if k=='h' else 'N',fontSize=size,leading=lead,
  textColor=GRAY if k=='small' else INK,wordWrap='CJK') for k,size,lead in
  [('body',10.5,17),('small',8.4,13),('h',12,19),('cell',9.2,15),('literal',10,17)]}
WRITTEN=[];LAYOUT=[]


def p(c,text,y,style='body',width=W,x=M,gap=9):
    text=str(text).replace('\u00b7',',')
    para=Paragraph(escape(text).replace('\n','<br/>'),STYLES[style]);_,h=para.wrap(width,900)
    if y-h<51:raise ValueError(('overflow',c.getPageNumber(),text[:100],y,h))
    para.drawOn(c,x,y-h);WRITTEN.append(text);LAYOUT.append({'page':c.getPageNumber(),'top':y,'bottom':y-h,'text':text[:100]})
    return y-h-gap


def page(c,title,subtitle):
    c.setFillColor(BLUE);c.rect(0,PH-17,PW,17,fill=1,stroke=0)
    c.setFillColor(GRAY);c.setFont('N',8.5);c.drawString(M,PH-43,'BenefitLens')
    c.setFillColor(INK);c.setFont('B',21);c.drawString(M,PH-80,title)
    WRITTEN.append(title);y=p(c,subtitle,PH-101,'small',gap=18)
    return y


def end(c):
    c.setFont('N',8);c.setFillColor(GRAY)
    c.drawRightString(PW-M,29,f'{c.getPageNumber():02d} / 12');c.showPage()


def flow(c,labels,y,caption):
    n=len(labels);gap=12;bw=(W-gap*(n-1))/n;h=69
    for i,label in enumerate(labels):
        x=M+i*(bw+gap);c.setFillColor(PALE);c.setStrokeColor(BLUE);c.roundRect(x,y-h,bw,h,5,fill=1,stroke=1)
        para=Paragraph(escape(label).replace('\n','<br/>'),STYLES['cell']);_,ht=para.wrap(bw-14,h-8)
        if ht>h-8:raise ValueError('flow node overflow')
        para.drawOn(c,x+7,y-h+(h-ht)/2);WRITTEN.append(label)
        if i<n-1:
            c.setStrokeColor(BLUE);c.line(x+bw+2,y-h/2,x+bw+gap-2,y-h/2)
            c.line(x+bw+gap-5,y-h/2+3,x+bw+gap-2,y-h/2)
            c.line(x+bw+gap-5,y-h/2-3,x+bw+gap-2,y-h/2)
    return p(c,caption,y-h-10,'small',gap=18)


def table(c,rows,widths,y):
    vals=[[Paragraph(escape(str(v)),STYLES['cell']) for v in row] for row in rows]
    t=Table(vals,colWidths=widths,hAlign='LEFT');t.setStyle(TableStyle([
      ('BACKGROUND',(0,0),(-1,0),PALE),('VALIGN',(0,0),(-1,-1),'TOP'),
      ('LINEBELOW',(0,0),(-1,0),.8,BLUE),('LINEBELOW',(0,1),(-1,-1),.3,HexColor('#ccd8dc')),
      ('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),
      ('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
    _,ht=t.wrap(W,800)
    if y-ht<52:raise ValueError('table overflow')
    t.drawOn(c,M,y-ht);WRITTEN.extend(str(v) for row in rows for v in row)
    return y-ht-15


def literal_page(c,title,t,subtitle):
    y=page(c,title,subtitle)
    y=p(c,'재료를 고르는 전략',y,'h')
    y=p(c,t['strategy']['material_selection_rule'],y)
    for i,para in enumerate(t['paragraphs'],1):
        y=p(c,f'문단 {i}  '+para['purpose'],y,'h',gap=5)
        y=p(c,para['opening']+' '+para['support']+' '+para['closing'],y,'literal',gap=5)
        if i<len(t['paragraphs']):y=p(c,'연결: '+para['bridge_meaning'],y,'small',gap=12)
    y=p(c,'기관 자원이나 후속 활동은 근거가 있을 때 선택 문장으로 추가합니다. 공식 문항의 순서와 분량을 먼저 적용합니다.',y,'small')
    end(c)


def main():
    a=e.load(OUT/'01-schema-audit.json');index=e.load(OUT/'02-index-result.json')
    qa=e.load(OUT/'09-diagnostics.json');ts=e.load(OUT/'06-templates-v2.json')['templates']
    drafts=e.load(OUT/'07-drafts.json')['drafts'];retrieval=e.load(OUT/'05-retrieval-reviewed.json')
    counts=qa['counts'];c=canvas.Canvas(str(OUT/'BenefitLens_스키마_검색_템플릿_초안_실행보고_12p.pdf'),pagesize=A4)
    c.setTitle('BenefitLens 스키마 검색 템플릿 초안 실행 보고')
    y=page(c,'공고에서 초기 초안까지 실행','공고 조건을 저장하고, 다른 프로그램의 작성 경험을 찾아, 재료와 문단 흐름을 연결했습니다.')
    y=flow(c,['공고 원문\n조건과 문항','유사한 작성 자료\n전체 글의 구성','전략과 빈칸 양식\n지원자 사실','초기 초안\n질문과 근거'],y,'그림 1. 글쓰기 자료의 사실을 지원자 이력으로 옮기지 않고 구성 방식만 참고합니다.')
    y=table(c,[['실행 범위','완료 결과'],['공고 색인',f"{counts['notice']:,}건, 원문 연결 {counts['notice_document']:,}개"],
      ['기존 조건 연결',f"{counts['condition_observation']:,}개, 의미 승인 전"],['이번 원문 분석','공고 3건, 고정 유형으로 정리한 조건 50개'],
      ['경험 검색 색인',f"경험 구절 {index['items']-16:,}개와 전체 구성 카드 16개"],
      ['전체 글 분석과 작성','전체 작성본 3건, 템플릿 3종, 초안 경로 2개']], [130,W-130],y)
    y=p(c,'전체 작성본 3건은 연구 제안서, 개인 기여 서술문, 개발 제안서입니다. 사용자 이력이 없는 경로는 빈칸과 추가 질문을 반환하고, 실제 문장이 이어지는지는 명시한 합성 이력으로 실행했습니다.',y)
    p(c,'이번 실행은 보관 원문의 재현 실행입니다. 공고 전체를 현재 접수 중으로 확인한 수치와 구분합니다.',y,'small');end(c)

    y=page(c,'조건 스키마는 어디까지 정리됐나','저장 구조, 기존 표현 연결, 실제 의미 해석의 완료 범위를 나눠 확인했습니다.')
    y=table(c,[['계층','실제 상태'],['고정된 분류 목록','42종. 특수 조건과 미확정 분류 포함'],
      ['기존 표현 연결',f"공통 {a['condition_namespaces_in_source']['common']:,}개, 특수 {a['condition_namespaces_in_source']['specific']:,}개"],
      ['공고별 해석 범위',f"기존 연결을 가진 공고 {qa['notices_with_condition_observations']:,}건"],
      ['이번 정규화 실행','공통 조건 노드 42개, 특수 조건 노드 8개'],
      ['개인 자격 판정','미검토 규칙은 unknown. 실행 가능한 승인 규칙 0개']], [130,W-130],y)
    y=p(c,'공통은 같은 의미 차원으로 비교할 수 있는 정보입니다. 특수는 연구 주제나 해당 사업의 의무처럼 범위를 보존해야 하는 절입니다. 같은 학위 조건이라도 적용 지역과 기준 시점은 조건마다 달라질 수 있습니다.',y)
    y=p(c,'NVIDIA의 박사 첫해 이수는 education.progress에 값 1, 단위 year로 저장했습니다. coursework 수료와 합치지 않았고, 예외적 지원 가능성은 별도 예외 객체로 남겼습니다.',y)
    y=p(c,'미연결 8,655건에는 고정 스냅샷에 완료된 조건 연결이 없다는 사유를 기록했습니다. 신규 조건은 자격, 제출, 평가, 수혜, 활동 의무를 구분하고, 금액이나 마감을 자격 역할로 저장하면 DB가 거부합니다.',y,'small')
    end(c)

    y=page(c,'테이블과 실제 값의 형태','관계형 테이블이 식별자와 연결을 맡고, 조건의 값과 범위는 JSON으로 저장합니다.')
    y=flow(c,['notice\n공고 식별자','notice_document\n원문 판본 연결','source_document\n경로와 해시'],y,'그림 2. 공고 26,417건 모두 원문 연결 행이 있으며, 공고가 같은 원문을 공유할 수 있습니다.')
    y=flow(c,['normalized_condition\n고정 필드와 값','source_span\n정확한 원문 위치','조건 범위와 논리\n지역, 예외, 시점'],y,'그림 3. 값의 출처를 원문 해시와 문자 위치로 되돌아갑니다.')
    example={'field_id':'education.progress','namespace':'common','value_type':'number','value':1,'unit':'year',
      'scope':{'degree':'doctorate','reference_phase':'before_application','operator':'gte','exception':'연구 준비 근거에 따른 심사 예외'},'executable':False}
    y=p(c,json.dumps(example,ensure_ascii=False,indent=2),y,'small')
    y=p(c,'학위는 enum이나 배열, 수치는 단위와 함께, 제출 서류는 문서 종류와 분량을 담은 객체로 저장합니다. OR와 예외는 별도 논리 구조에 둡니다. 문장을 정규식으로 잘라 같은 필드로 간주하는 방식으로 의미를 확정하지 않습니다.',y)
    p(c,'SQLite 실행 DB에 FK와 JSON 유효성 제약을 적용했습니다. 운영 PostgreSQL 원본은 변경하지 않았습니다.',y,'small');end(c)

    y=page(c,'공고와 경험을 가져오는 경로','현재 저장된 원문을 입력으로 삼고, 새 원문은 같은 검증 경로로 들어오도록 연결합니다.')
    y=flow(c,['공개 API, HTML\nPDF 수집','텍스트 추출\n필요하면 OCR','해시와 원문 검증\n완료 자료 선별'],y,'그림 4. 기존 Python 수집기와 원문 무결성 뷰를 새 파이프라인의 입력으로 사용합니다.')
    y=flow(c,['공고의 전체 텍스트\n자격, 제출, 평가','LLM 의미 해석\n고정 유형과 범위','SQL 저장\n정확한 근거 위치'],y,'그림 5. 수혜 내용과 제출 의무를 개인 자격 조건으로 섞지 않습니다.')
    y=p(c,'공고는 API 응답, HTML 본문, PDF 원문에서 확보하고, 경험은 공개 작성본, 인터뷰, 후기의 출처와 문서 역할을 함께 보관합니다. 출처마다 같은 방식으로 확보할 수 있는지는 기존 수집기가 판별합니다.',y)
    y=p(c,'갱신 정책은 하루 단위의 재확인과 원문 변경 감지입니다. 새 해시가 생기면 조건 해석과 벡터를 새 판본으로 생성하고, 원문의 마감 상태를 확인한 뒤 사용자에게 노출합니다. 이번 작업에서 새 일일 스케줄러를 설치하지는 않았습니다.',y)
    p(c,'실행 명령의 prepare는 검증된 원문 뷰를 읽습니다. 원문이 변하면 새 출력 디렉터리와 판본으로 실행하며, 이전 LLM 결과를 다른 입력에 재사용하지 않습니다.',y,'small');end(c)

    y=page(c,'문장 조각에서 전체 글의 전략으로','전체 문서를 읽은 결과와 문단 경계를 추정한 결과를 구분합니다.')
    y=table(c,[['전체 작성본','구성 해석'],['D01 NVIDIA 연구 제안서','연구 목표와 과제 → 본인의 컴파일러 작업과 적용 사례 → 다음 연구'],
      ['D02 NSF 개인 서술문','문제의식과 연구 경험 → 참여의 장벽에 대한 관찰 → 넓은 기여'],
      ['D03 GSoC 개발 제안서','프로젝트 필요 → 구현 범위와 준비 → 작업 순서와 검증']], [155,W-155],y)
    y=flow(c,['전체 텍스트\n3건 모두 입력','주장과 재료 선택\n왜 이 경험인가','문단의 역할과 연결\n17개 논증 블록'],y,'그림 6. 블록은 분석을 위해 묶은 단위이며 PDF의 실제 문단 경계와 같다고 간주하지 않습니다.')
    y=p(c,'전략은 읽는 사람이 무엇을 판단하도록 어떤 경험을 고를지 정합니다. 전술은 그 경험을 어느 순서와 문장 양식으로 제시할지 정합니다. 기존 작업을 다음 과제의 준비 근거로 쓰는 것은 전략이고, 앞 문단의 질문을 다음 첫 문장에서 받는 것은 전술입니다.',y)
    y=table(c,[['경험과 글쓰기 테이블','저장 내용'],['source_document','전체 텍스트, 원문 경로, 프로그램, 해시'],
      ['semantic_document','독자 질문, 핵심 주장, 재료 선택, 순서 있는 논증 블록'],
      ['strategy_template','장르, 선택 전략, 실제 문장 양식, 슬롯 출처와 시제'],
      ['draft_slot_value와 문장 연결','채운 값, 사실 ID, 시제와 미입력 상태']], [175,W-175],y)
    p(c,'공개 작성본의 수행 결과는 저자의 서술입니다. 그 구성 때문에 선정됐다는 효과와 저자가 쓰지 않은 재료는 확인된 정보에 포함하지 않습니다.',y,'small');end(c)

    y=page(c,'공고와 경험의 임베딩 검색','동일한 프로그램의 경험이 없어도 작성 목적과 장르가 맞는 자료를 찾습니다.')
    y=flow(c,['공고의 작성 문항\n자격 질문 분리','문항 의도 임베딩\n384차원','경험 임베딩 검색\n장르와 역할 필터','원문 관련성 확인\n전체 글 불러오기'],y,'그림 7. 자격 조건 검색은 SQL과 규칙 경로, 글쓰기 자료 검색은 임베딩 경로를 사용합니다.')
    y=p(c,f"공고 벡터 {index['notice_vectors']:,}개 중 {index['notice_with_conditions']:,}개는 조건 요약을 포함하고, {index['notice_title_only']:,}개는 제목만 포함합니다. 이 벡터는 개인의 지원 가능 여부를 뜻하지 않습니다.",y)
    y=p(c,'벡터는 공용 캐시의 NumPy 배열에, 원문 식별자와 검색 연결은 SQL에 저장합니다.',y,'small')
    y=p(c,'처음에는 일반 안내와 목차가 상위에 나왔습니다. 목차와 너무 짧은 반응 등 3,849개를 작성 검색에서 제외하고, 작성 문항만 다시 추출했습니다. CV만 요구한 인턴 공고에는 에세이 구조를 붙이지 않았습니다.',y)
    y=table(c,[['작성 문항','실제 전체 구성 참고'],['Google 연구 제안서','NVIDIA 연구 제안서 D01'],['Google 기여와 리더십','NSF 개인 서술문 D02'],['처음 보는 연구 기회','NVIDIA 연구 제안서 D01']], [220,W-220],y)
    accepted=sum(len(q['material_hits']) for q in retrieval)
    p(c,f'6개 작성 질의의 구절 후보는 원문 관련성을 다시 검토해 {accepted}개를 채택했습니다. 코사인 점수는 유사도이며, 선정 확률이나 독립 평가한 검색 정확도는 측정하지 않았습니다.',y,'small');end(c)

    literal_page(c,'연구 제안서의 빈칸형 문단',next(t for t in ts if t['genre']=='research_proposal'),'하나의 경험에서 관찰, 본인 기여, 남은 질문, 검증을 이어 쓰는 편집 양식입니다.')
    literal_page(c,'개인 기여의 빈칸형 문단',next(t for t in ts if t['genre']=='contribution_statement'),'경험의 상황과 판단을 상대가 평가하려는 기여에 연결합니다.')
    literal_page(c,'실행 제안의 빈칸형 문단',next(t for t in ts if t['genre']=='execution_proposal'),'필요, 최소 결과물, 의존 관계, 검수와 인계를 연결합니다.')

    d=next(d for d in drafts if d['subject_id']=='synthetic-student')
    y=page(c,'실제로 생성된 초기 초안','아래 40개, 12개, 100개는 실행 검증을 위해 명시한 합성 이력의 값입니다. 실제 사용자 이력이 아닙니다.')
    for i,para in enumerate(d['paragraphs'],1):
        y=p(c,f'문단 {i}',y,'h',gap=5)
        y=p(c,para['opening']+' '+para['support']+' '+para['closing'],y,'literal',gap=5)
        y=p(c,'지원자 근거: '+', '.join(para['used_fact_ids'])+' / 다음 연결: '+para['bridge_to_next'],y,'small',gap=11)
    y=p(c,'재료 선택: F1과 F2는 문제의 관찰, F3은 준비 근거, F4는 다음 검증에 사용했습니다. F5의 예외 조건 확대는 후속 범위로 남겼습니다.',y,'small')
    p(c,'LLM이 먼저 빈칸과 근거, 시제를 연결하고 문장은 템플릿에서 조립합니다. 입력이 없는 필수값은 빈칸으로 남겨 초안 상태를 incomplete로 표시합니다.',y,'small');end(c)

    d=next(d for d in drafts if d['subject_id']=='real-user')
    y=page(c,'사용자 정보가 없을 때의 출력','글을 만들기 전에 모든 정보를 요구하지 않고, 선택한 문항의 빈칸을 채우는 질문을 반환합니다.')
    y=flow(c,['초기 탐색 정보\n학위, 학적, 전공 등','선택한 공고의 특약\n필요한 정보만 질문','본인의 경험과 계획\n근거가 있는 내용','초안과 추가 질문\n부족한 항목 표시'],y,'그림 8. 초기 계약은 학교, 학위, 학적, 전공과 검색 선호 두 가지로 구성되며 모두 선택 입력입니다.')
    first=d['paragraphs'][0]
    y=p(c,'빈칸 출력의 첫 문단',y,'h')
    y=p(c,first['opening']+' '+first['support']+' '+first['closing'],y,'literal')
    y=p(c,'초안 작성을 위한 질문 예시',y,'h')
    for question in d['missing_information_questions'][:5]:y=p(c,'- '+question,y)
    p(c,'공개 자료는 문단 구조의 참고로만 전달합니다. 본인 행동 슬롯에는 applicant_record 출처만 허용하고, 근거가 없는 기관 자원 문장은 생략합니다.',y,'small');end(c)

    y=page(c,'검토와 재실행 결과','계획한 기능, 실행한 범위, 측정하지 않은 품질을 구분해 남깁니다.')
    y=table(c,[['Claude 검토','구체적으로 바꾼 것'],['스키마','공고와 원문 FK, 정확한 근거 위치, 지원자 사실의 주체 제약 추가'],
      ['분석과 검색','자격과 작성 문항 분리, 지역별 적용 범위 저장, 페이지 구성 문구 제거'],
      ['전략과 양식','한 경험으로 작성, 선택 문장 생략, 슬롯 출처와 문단 연결 재작성'],
      ['초안과 통합','문장별 근거와 시제를 연결하고, 미입력 필수값을 빈칸으로 유지'],
      ['수정 결과 재확인','최종 초안, 슬롯 연결, 문맥 관계와 수정 근거를 재검토']], [125,W-125],y)
    y=p(c,f"이번 조건 노드를 뒷받침하는 원문 구절 {qa['source_spans_checked']}개를 검사했고, FK 오류 {qa['foreign_key_errors']}건입니다. 핵심 테스트 22개가 통과했습니다. Claude 검토 5회와 별도 판단으로 문장 간 의미 연결을 수정했습니다.",y)
    y=p(c,'실행 코드: tools/benefitlens_end_to_end.py\n양식과 슬롯 검사: tools/benefitlens_literal_templates.py\n검증: tools/benefitlens_pipeline_diagnostics.py\n재실행: python3 tools/benefitlens_end_to_end.py all --output 새경로',y,'small')
    y=p(c,'모델: intfloat/multilingual-e5-small, 고정 revision과 원문 해시는 실행 파일에 기록했습니다. 모델 설명은 https://huggingface.co/intfloat/multilingual-e5-small 에서 확인할 수 있습니다.',y,'small')
    p(c,'이 실행은 선택한 작성본 3건을 끝까지 연결한 결과입니다. 보유 경험 전체의 문서 단위 분석, 미검토 조건의 자격 판정 승인, 실제 지원자를 대상으로 한 글 품질 평가는 별도 범위로 남습니다.',y,'small');end(c)
    c.save()
    text='\n'.join(WRITTEN);assert len(text)<10000,(len(text),'report exceeds user limit')
    assert '\u00b7' not in text
    (OUT/'보고서본문.txt').write_text(text)
    e.dump(OUT/'report-layout.json',LAYOUT)
    pdf=pymupdf.open(OUT/'BenefitLens_스키마_검색_템플릿_초안_실행보고_12p.pdf')
    assert len(pdf)==12
    extracted_characters=sum(len(page_obj.get_text()) for page_obj in pdf)
    assert extracted_characters<10000,extracted_characters
    renders=OUT/'qa';renders.mkdir(exist_ok=True)
    for i,page_obj in enumerate(pdf):page_obj.get_pixmap(matrix=pymupdf.Matrix(1.2,1.2)).save(renders/f'page-{i+1:02}.png')
    e.dump(OUT/'report-build.json',{'pages':len(pdf),'authored_characters':len(text),'extracted_characters':extracted_characters,'layout_checks':len(LAYOUT),
      'visual_review_required':True,'reason':'PDF rendering checks font painting, clipping and layout not established by text extraction.'})
    print('Rendered 12 pages;',len(text),'characters')


if __name__=='__main__':main()
