"""Publish the measured corpus audit and an explicitly proposed writing rubric."""
import json
from pathlib import Path
from html import escape as esc

from audit_benefitlens_writing_foundations import authored,page,REPO
from analyze_benefitlens_writing_patterns import OUTPUT
from collect_benefitlens_writing_foundations import ROOT


def main():
    analysis=json.loads((OUTPUT/'analysis.json').read_text())
    taxonomy=json.loads((REPO/'pipeline/config/writing_pattern_taxonomy_v1.json').read_text())
    rubric=json.loads((REPO/'pipeline/config/writing_quality_rubric_v1.json').read_text())
    readings=json.loads((OUTPUT/'source-readings.json').read_text())
    out=REPO.parents[2]/'outputs/benefitlens';out.mkdir(exist_ok=True)
    report=out/'BenefitLens_작성패턴_분류와판정기준_20261117.txt'
    reader=out/'BenefitLens_작성패턴_분류와판정기준_20261117.html'
    high=analysis['persuade']['groups']['score_5_or_6'];low=analysis['persuade']['groups']['score_1_or_2']
    matched=analysis['revision']['comparisons']['human_test_exact_pair_overlap']
    text=f'''BenefitLens 작성 패턴의 분류와 판정 기준
분석 기준: {analysis['at_kst']}

판단
현재 자료로 작성 패턴의 분류 체계와 검토 기준 초안을 지정할 수 있다. 내부 표현은 문항, 경험 선택, 글의 구성, 문단의 기능, 연결 관계와 문장 수정이라는 6개 수준으로 나눈다. 여기에 문항별 패턴 6종과 품질 기준 9개를 조합한다. 프로그램별 선발 효과와 기준의 가중치를 정하려면 해당 문항의 평가 자료가 더 필요하다.

1. 실제로 분석한 범위
PERSUADE 완성 글 10,402편의 원 논증 주석, ELLIPSE 6,482편의 원 채점과 수정 자료의 사람 주석, 모델 분류를 전수 집계했다. 두 완성 글 코퍼스의 중복 303편은 앞선 수량 집계에서 제외했다. 이번에는 모집단과 척도가 달라 각 코퍼스를 따로 분석했다.
실제 지원 자료에서는 Jean Yang의 NSF GRFP와 박사 지원 글, LFX Harbor와 C4GT Zendalona의 작성 부분을 독해하고 8개 구절의 원문 위치를 검증했다. 기존 문항별 조언 6종과 원문이 확인된 일반 원칙 8개도 분류 근거로 사용했다. 전체 공개 지원서의 의미 분류나 일괄 LLM 분석은 실행하지 않았다.

2. 자료에서 확인한 사실과 그 의미
첫째, 근거의 존재만으로 글의 질을 구분하기 어렵다. PERSUADE의 1~2점 글 {low['essays']:,}편 중 {low['role_presence']['Evidence']['count']:,}편({low['role_presence']['Evidence']['percent']}%)에 근거 역할로 주석된 부분이 있었다. 5~6점 글 {high['essays']:,}편에는 모두 그 부분이 있었다. 평가 항목은 근거가 있느냐보다 주장과의 관련성, 구체성, 설명의 충분성으로 정하는 편이 타당하다.
둘째, 반론은 조건부 구성이다. 5~6점 글 중 반론 역할이 없는 글은 {high['essays']-high['role_presence']['Counterclaim']['count']:,}편({100-high['role_presence']['Counterclaim']['percent']:.2f}%)이었다. 논쟁이나 대안 비교를 요구할 때 선택하고 개인 기여나 지원 동기 문항에 일괄 강제하지 않는다.
셋째, 자주 등장하는 구조는 작성 후보를 제공한다. 인접한 비중첩 주석에서 Claim→Evidence는 20,550회, Evidence→Claim은 13,782회였다. 이는 주장과 근거를 반복해 전개하는 구조의 관찰값이다. 이 집계로 어느 순서가 점수를 올렸는지 판단할 수는 없다. 고득점군과 저득점군의 단어 수 중앙값도 각각 {high['median_words']}, {low['median_words']}으로 달랐다. 과제와 길이가 섞여 있어 빈도나 단어 수를 선발 기준으로 사용하지 않는다.
넷째, 언어의 여러 차원은 함께 움직인다. ELLIPSE에서 종합 점수와 6개 언어 차원의 Spearman 상관은 0.7466~0.7974였다. 문법, 연결성, 어휘를 설명하는 진단 항목으로 유지하되 이 값으로 지원서의 가중치를 만들지 않는다.
다섯째, 수정 목적과 품질은 따로 판단해야 한다. IteraTeR의 사람 테스트 364행 중 배포된 자동 라벨과 동일한 수정 전후 쌍을 가진 {matched['matched_pairs']}건의 라벨 일치율은 {100*matched['agreement']:.2f}%였다. 이 부분집합에서 연결성 F1은 {matched['per_label']['coherence']['f1']}, 문체 F1은 {matched['per_label']['style']['f1']}이었다. 모든 분할을 섞은 일치율에는 훈련 자료도 포함된다. 실제 문장에서도 enables를 enabling으로 바꿔 동사 병렬 구조가 깨진 수정이 있었다. 자동 라벨과 confidence는 검토 후보를 찾는 자료로 보관한다.

3. 전체 작성 패턴의 내부 구분
'''
    for level in taxonomy['levels']:
        text+=f"- {level['name']}: {level['question']}\n"
    text+='''패턴 하나는 문항 의도, 글 유형, 선택 재료, 문단별 목적, 첫 문장의 기능, 뒷받침, 의미 연결, 마지막 문장의 기능과 다음 문단과의 관계로 표현한다. 한 문서에는 여러 패턴이 들어갈 수 있다.

4. 문항별 기본 패턴 6종
'''
    for p in taxonomy['patterns']:
        text+=f"- {p['name']}: 시작은 {p['opening_intent']} 재료는 {p['material_selection']} 마무리는 {p['closing_intent']}\n"
    text+='''첫 문장을 항상 주장으로 고정하지 않는다. 같은 저자의 NSF GRFP 글은 교육 장면으로 시작해 펠로십의 활용으로 닫고, 박사 지원 글은 연구 목표를 먼저 밝힌 뒤 학교의 자원과 연결했다. 첫 문장과 마지막 문장의 기능을 기록하면 서로 다른 전개를 같은 체계로 다룰 수 있다.
LFX Harbor 자료는 지원 이유, 관련 경험, 기대 학습과 구현 계획을 질문별로 구분한다. 경험을 설명하는 부분과 계획을 제시하는 부분에는 서로 다른 기준을 적용한다.
C4GT 자료는 TuxType와 TuxMath를 계획 대상으로 적었고 본인 소개에서는 Maths-Tutor라는 명칭을 사용했다. 동일 대상을 가리키는지 확인할 일관성 검토 항목이다. 세부 일정이 있다는 사실만으로 내용의 적합성을 승인하지 않는다.

5. 품질 기준 9개
각 기준은 관찰한 범위에서 0=부족, 1=부분 충족, 2=구체적 충족으로 기록한다. 입력이 없거나 문맥이 부족하면 관찰 불가, 문항에 필요 없으면 해당 없음으로 기록하고 점수는 null로 둔다.
'''
    for c in rubric['criteria']:
        text+=f"- {c['name']}: 2점 기준은 {c['2']}\n"
    text+='''문단별 기본 예시는 다음과 같다. 대괄호는 사용자가 확인할 사실이다.
첫 문장: [과제]에서 저는 [책임]을 맡았습니다.
뒷받침: [제약] 때문에 [방법]을 선택했고 [행동]을 수행했습니다.
근거와 해석: [결과 기록]에서 [변화]를 확인했으며, 이는 [주장의 범위]를 보여 줍니다.
마지막 문장: 이 경험을 바탕으로 [다음 과제]에서 [활용할 판단]을 적용하려 합니다.
다음 문단은 그 과제를 수행할 프로그램 자원이나 실행 계획으로 이어진다. 이는 기여를 묻는 문항의 편집 예시다.

6. 내부 적용 방식
먼저 실제 문항에서 요구 항목을 읽고 필요한 패턴을 선택한다. 개인 자료나 사용자 확인에서 경험과 사실을 가져와 각 역할에 배치한다. 문단별 시작, 뒷받침, 해석과 마무리를 만들고 문단 사이의 관계를 검토한다. 표현 수정은 의미, 문법과 연결성을 대조해 채택한다.
평가는 기준별 원문 위치와 이유를 남긴다. 합계, 가중치와 자동 합격점을 만들지 않는다. 개인 사실의 출처, 프로그램명과 수치의 일관성, 실제 제출 조건을 별도로 확인한다. 필요한 개인 사실이 없으면 입력 요청이나 자리표시자로 남긴다.
패턴 근거에는 원문 ID, 인용 위치, 문맥, 장르, 반례와 적용 범위를 붙인다. 문서 수, 알려진 작성자 수, 프로그램 수와 회차 수를 구분한다. 한 사람이 여러 학교에 낸 글을 독립된 여러 사람의 반복 증거로 계산하지 않는다.

7. 지정한 것과 남은 검증
분류 명세 6수준, 문항별 기본 패턴 6종, 판단 기준 9개와 필수 확인 3개를 파일로 작성했다. 기준은 원문과 관찰에 근거한 내부 초안이다. 금전적 필요와 지원금 사용 계획, 한국 지원서의 프로그램별 표현은 근거 보강이 필요하다.
다음 검증은 실제 문항별로 초안 두 개를 가리고 비교해 요구 충족, 사실 보존, 구성과 작성 부담을 평가하는 방식이 적절하다. 작성자와 회차가 겹치지 않게 평가 자료를 나누고 평가자 간 불일치를 검토한다. 현재는 독립 평가와 점수 보정 결과가 없다.

파일
분류: pipeline/config/writing_pattern_taxonomy_v1.json
판정: pipeline/config/writing_quality_rubric_v1.json
분석 지시: pipeline/config/operational_writing_pattern_prompt_v5.txt
정량 결과: 공용 캐시 writing-foundations-v1/pattern-analysis-v1/analysis.json
원문 독해: 같은 폴더 source-readings.json
실행: /usr/bin/python3 tools/analyze_benefitlens_writing_patterns.py
보고서 갱신: /usr/bin/python3 tools/report_benefitlens_pattern_analysis.py

원전
https://github.com/scrosseye/persuade_corpus_2.0
https://github.com/scrosseye/ELLIPSE-Corpus
https://aclanthology.org/2022.acl-long.250/
https://saylordotorg.github.io/text_writing-for-success/index.html
'''
    authored(report,text)
    levels=''.join('<tr><td>'+esc(x['name'])+'</td><td>'+esc(x['question'])+'</td></tr>' for x in taxonomy['levels'])
    patterns=''.join('<details><summary>'+esc(p['name'])+'</summary><p><strong>시작:</strong> '+esc(p['opening_intent'])+'</p><p><strong>재료:</strong> '+esc(p['material_selection'])+'</p><p><strong>마무리:</strong> '+esc(p['closing_intent'])+'</p><p><strong>다음 문단:</strong> '+esc(p['bridge'])+'</p></details>' for p in taxonomy['patterns'])
    criteria=''.join('<tr><td>'+esc(c['name'])+'</td><td>'+esc(c['2'])+'</td></tr>' for c in rubric['criteria'])
    evidence=''.join('<details><summary>'+esc(r['document_id'].split(':',1)[1])+'</summary><p>'+esc(r['source_observation'])+'</p><p>'+esc(r['editorial_judgment'])+'</p><a href="'+esc(r['url'],quote=True)+'">원문</a></details>' for r in readings)
    body=f'''<p>작성 패턴을 6개 수준에서 구분하고 문항별 패턴 6종, 품질 기준 9개를 조합하는 내부 초안입니다.</p>
<article><h2>관찰한 사실</h2><p>낮은 점수 글의 근거 역할 표시: <strong>98.9%</strong>. 근거의 구체성과 관련성을 평가해야 합니다.</p><p>높은 점수 글에서 반론 역할이 없는 비율: <strong>44.58%</strong>. 문항에 따라 반론을 선택합니다.</p><p>정확히 매칭된 수정 테스트 309건의 연결성 F1: <strong>0.3333</strong>, 문체 F1: <strong>0.1333</strong>. 자동 라벨을 검토 후보로 사용합니다.</p><p>정량 분석은 원 주석과 채점의 집계입니다. 실제 지원 자료 4개의 해당 부분도 독해했습니다.</p></article>
<h2>분류 체계</h2><table><tr><th>수준</th><th>판단할 질문</th></tr>{levels}</table>
<h2>문항별 작성 패턴</h2><p>항목을 펼치면 첫 문장, 재료 선택, 마지막 문장과 다음 문단의 기능을 볼 수 있습니다.</p>{patterns}
<h2>판정 기준</h2><p>0=부족, 1=부분 충족, 2=구체적 충족. 관찰 불가와 해당 없음은 null로 기록합니다. 총점과 가중치는 보정 전입니다.</p><table><tr><th>기준</th><th>구체적으로 충족한 상태</th></tr>{criteria}</table>
<h2>실제 원문에서 관찰한 구성</h2>{evidence}
<h2>문단 구성의 적용 예시</h2><article><p><strong>시작:</strong> [과제]에서 제가 맡은 [책임]을 밝힌다.</p><p><strong>전개:</strong> [제약] → [선택한 방법] → [본인이 수행한 행동] → [확인된 결과] → [그 결과의 의미].</p><p><strong>마무리:</strong> 보여 준 역량을 다음 과제와 연결한다.</p><p><strong>다음 문단:</strong> 그 과제에 필요한 프로그램 자원이나 실행 계획을 설명한다.</p></article>
<p>사실 출처, 명칭과 수치의 일관성, 제출 조건은 별도로 확인합니다. 적용할 개인 사실이 부족하면 입력 요청이나 자리표시자로 남깁니다.</p>
<h2>현재 적용 범위</h2><p>일반적인 글의 구성과 표현, 문항별 기본 구조의 기준을 정했습니다. 프로그램별 선발 효과, 금전적 필요 문항, 한국 지원서 유형과 독립 평가가 후속 검증 범위입니다.</p>
<p><a href="{esc(str(report),quote=True)}">분석 보고서</a> / <a href="{esc(str(OUTPUT/'analysis.json'),quote=True)}">계산 결과와 원본 DB 해시</a> / <a href="{esc(str(REPO/'pipeline/config/writing_pattern_taxonomy_v1.json'),quote=True)}">분류 명세</a> / <a href="{esc(str(REPO/'pipeline/config/writing_quality_rubric_v1.json'),quote=True)}">판정 명세</a></p>
<p>원전: <a href="https://github.com/scrosseye/persuade_corpus_2.0">PERSUADE</a>, <a href="https://github.com/scrosseye/ELLIPSE-Corpus">ELLIPSE</a>, <a href="https://aclanthology.org/2022.acl-long.250/">IteraTeR 논문</a>.</p>'''
    authored(reader,page('BenefitLens 작성 패턴의 분류와 판정 기준',body))
    print(json.dumps({'report':str(report),'reader':str(reader),'report_characters':len(text)},ensure_ascii=False))


if __name__=='__main__':main()
