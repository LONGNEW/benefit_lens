# 최종 평가 자료
기준일: 2026-11-04. 현재 파일과 앞으로 작성할 파일을 구분한다.

## 지금 있는 파일

| 파일 | 내용 | 상태 |
| --- | --- | --- |
| catalog_audit.csv | 현재 10개 공고의 원문 연결, 조건과 프로그램 지식 범위 | 공개 표본을 직접 재집계 |
| storage_contract_check.json | 저장 계약 검사 10/10 결과 | 11월 4일 읽기 전용 실행. 모델 호출과 DB 쓰기 없음 |
| metrics.csv | 지표, 현재 값, 분자와 분모, 시점과 측정 상태 | 의미 정확도와 사용자 효과는 빈 값, unmeasured |
| gold_annotations.template.jsonl | 10개 공고의 주석 시작점 | gold는 null, 검토자는 미배정. 정답 데이터가 아님 |

현재 표본은 이미 개발에 사용했으므로 regression으로 표시한다. 원문 해시가 없는 6개는 summary_only다. 정답을 작성하기 전에는 현재 모델/검토 표본을 gold로 복사하지 않는다.

source_backed는 공개 표본에 source_id와 원문 content_sha256이 연결돼 있다는 뜻이다. 오늘 원본 파일과 인용을 전부 다시 검사했다는 뜻은 아니다. 공고 4개와 경험 25개의 원문 재처리 기록은 10월 18일 실행 결과로 별도 제시한다.

## 다음 산출물의 형식

아래 파일들은 아직 생성하지 않았다.

| 파일 | 필수 내용 |
| --- | --- |
| annotation_guideline.md | 조건 단위, 시점과 예외, 누락/모호 상태, 합의 기준 |
| gold_annotations.jsonl | 원문 해시, 인용 위치, 의미 항목과 논리 경로, 두 검토와 합의 |
| predictions.jsonl | run_id, 모델과 프롬프트, 입력 해시, 원래 출력과 검증 결과 |
| field_errors.csv | item_id, 필드/조건 경로, gold, prediction, TP/FP/FN, 오류 유형과 검토자 |
| eligibility_cases.jsonl | 원문 정답과 독립 프로필, 기대 상태, 실제 상태와 판정 경로 |
| writing_review.csv | 문항 요구, 사용한 본인 사실, 결과 구절, 충족과 추가 사실, 분량 |
| participant_results.csv | 익명 참가자, 과제 순서, 사전 인지, 관련 후보, 판단/작성 시간과 오류 |
| refresh_runs.jsonl | 실행 시각, 출처, 성공/실패, 전후 해시, 변경/불변, 재시도와 지연 |
| db_benchmark.csv | 데이터 단위별 규모, 질의와 DB 버전, 인덱스, 캐시 조건, 5회 시간 |
| decision_log.md | 변경 전후 범위와 기준, 이유, 시점과 검토자 |
| final_summary.md | 위 파일에서 계산한 지표와 원수, 표본 범위와 실패 |

파생 지표를 계산하기 전에 정답과 출력의 버전을 고정한다. 분모가 없거나 아직 실행하지 않은 값은 0이나 100%로 채우지 않는다.

## 규칙과 실행

[평가 규약](../../proposal/07_평가_계획과_채점_기준.md), [결과물 계약](../../proposal/12_최종_완료_계약과_결과물_형식.md), [공식 요구](../../proposal/11_과제_요구사항_충족_계획.md)를 따른다.

현재 의미 정확도 평가를 수행하는 통합 CLI는 없다. 존재하지 않는 실행 명령을 제출 문서에 적지 않는다. tools/run_pipeline_demo.py와 make pipeline-replay는 기존 원문/모델 결과, DB와 HTTP 동작을 재검증하는 경로이며 DB에 쓰기 때문에 실행 환경 확인 후 사용한다.

이번 저장 계약 검사는 pipeline/src를 PYTHONPATH에 두고 data_platform.demo_contract.validate_notice를 공개 표본 10개에 호출했다. 원문 의미, 모델 추출 품질과 사용자 효과를 검사하지 않았다.

## 신규 개인화 경로의 계약 예시

[개인화 명세](../../proposal/13_개인화_근거수집과_작성지원_명세.md)의 개인 자료와 공개 검색, 요구 기반 사실 확보, 서류 상태와 작성 지침은 [합성 계약 예시](../spec_examples/README.md)에서 확인한다. 합성 검사 통과를 실제 사용자 검색이나 내용 정확도 결과에 합산하지 않는다. 관련 지표는 metrics.csv에 미측정으로 추가한다.
