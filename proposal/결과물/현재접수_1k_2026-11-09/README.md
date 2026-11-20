# BenefitLens 현재 접수 공고 1천 건

한국시간 2026년 11월 9일 11시 50분~12시 00분에 공식 원천을 조회하고, 12시 02분에 최종 검산했다. **당일 접수 상태와 학생 대상 근거를 확인한 공고 1,344건에서 1,000건을 선정했다.** 장학금 117건과 인턴십 883건이다. [전체 검산](audit_final.json)

| 공식 원천 | 선정 공고 |
| --- | ---: |
| Amazon | 180 |
| Bosch | 180 |
| CERN | 21 |
| ESA | 175 |
| University of Queensland | 117 |
| Intel | 83 |
| NVIDIA | 164 |
| Siemens | 80 |
| 합계 | 1,000 |

작은 원천의 검증된 공고를 유지하도록 원천별로 번갈아 선택했다. 원천 고유 ID를 공고 식별자로 사용했고, 여러 근무지나 첨부파일을 새 공고로 늘리지 않았다. UQ의 개별 명명 장학금 117건은 지원폼 3개를 공유한다. 전체 공고 URL은 1,000개이고 지원 진입 URL은 886개다. [공식 원문 예시](examples.md)

## 현재 접수 판정

공식 현재 목록 등재와 상세 원문의 학생 조건을 확인했다. Workday에서는 원본의 `canApply: true`와 `posted: true`, SmartRecruiters에서는 `active: true`와 `visibility: PUBLIC`을 대조했다. 공개 HTML은 실제 `Apply now` 또는 `Apply` 링크를 추출했다. Amazon은 상세의 `a#apply-button` 주소로 지원 URL을 확정했다.

마감과 활동 시점도 따로 검토했다. 현재 목록에 있어도 신청 마감이 지났거나, 이미 시작한 고정 회차 또는 충돌하는 연도를 명시한 공고는 제외하거나 보류했다. 기업 창립 연도, 과거 팀 소개, 채용 검토 개시일은 신청 마감으로 해석하지 않았다. [수집팀 검증 기록](/home/longnew/Desktop/course_work/tmp/benefitlens_live_1k_20261109/discovery/delivery_report.md), [Workday 검증 기록](/home/longnew/Desktop/course_work/tmp/benefitlens_live_1k_20261109/schema/team_report.md)

선정한 320건은 원문 마감 날짜와 정확한 인용 위치가 있다. 680건은 마감 날짜를 확정하지 못했으며, 당일 공식 목록과 공개 지원 상태를 근거로 선정했다. 각 행에 확인 시각과 판정 근거를 저장했다. 시간 경과에 따른 재확인은 새로운 수집 실행이 담당한다.

UQ는 공식 `Open` 배지와 현재 접수기간, 지원 링크를 대조한 129건에서 공개 양식 첫 페이지까지 확인한 117건을 선정했다. 로그인 1건, 자바스크립트 초기 화면만 받은 8건, 국내학생과 국제학생의 마감이 다른 MBA 3건은 보류했다. [양식 검증 보고](/home/longnew/Desktop/course_work/tmp/benefitlens_live_1k_20261109/quality/form_check_report.md)

## 코드와 DB 검증

기존 SQL 스키마를 유지하고, 원문, 학생 대상 항목, 현재 접수 상태와 근거를 같은 공고에 연결했다. 신규 코호트의 `--current-open-only` 적재 모드는 명시한 입력만 사용한다. 당일 확인, `open`, 신청 날짜 구간, 지원 URL과 공식 원문 근거를 검사한다. 이전 과거 기록은 별도 아카이브 경로에 보존했다.

PostgreSQL 16.15 검증에서 현재 공고 1,000건, 주장 2,000개, 근거 위치 2,320개가 적재됐다. 원본 해시 1,000개와 추출문 해시 1,000개가 일치했고, 같은 입력으로 재적재한 결과가 같았으며 무결성 위반은 0건이었다. [SQL 검증](verification.json)

독립 코드 검토에서 마감 날짜만 넣어 공개 기한으로 표시할 수 있던 검증 공백을 수정했다. 알려진 마감에는 원문 인용과 위치를 필수화했고, 해당 반례를 포함한 테스트 9개가 통과했다. 수정된 검증으로 선정 1,000건을 다시 검사했다. [독립 검토](/home/longnew/Desktop/course_work/tmp/benefitlens_live_1k_20261109/schema/independent_review.md)

이 단계는 현재 모집 공고와 학생 대상 근거의 수집 결과다. 개인별 전체 자격과 실제 지원서 제출 결과는 원문 조건 및 후속 신청 과정에서 확인할 항목이다. 기존 경험 사례 7건과 작성 패턴 6건은 이전 데이터에 보존했다. 신규 1천 건의 작성 경험과 패턴은 추가 수집하지 않았다.

## 과거 자료를 활용하는 작성 지원

| 자료 | 확보할 정보 | 연결 용도 |
| --- | --- | --- |
| 과거 공고, 공식 양식과 작성 안내 | 당시 자격, 문항, 제출 요건과 평가 기준 | 현재 회차와 변경점 대조 |
| 실제 공개 지원서, 에세이와 연구계획서 | 경험을 선택한 방식, 근거 제시와 글의 전개 | 문항별 구조와 보강 질문 도출 |
| 후기, 인터뷰와 작성자 회고 | 준비 과정, 강조한 경험과 본인이 돌아본 점 | 작성 지침과 주의점 도출 |

과거 USAJOBS 984건은 공고별 자료를 찾는 색인으로 활용한다. 연결할 지원서나 인터뷰 원문을 확보하면 실제 사례로 등록하고, 결과가 확인되면 선정 여부와 그 근거를 기록한다. 기존 사례 7건과 패턴 6건도 작성 참고 자료로 유지한다.

DB에는 자료가 나온 source_round_id와 지침을 적용할 target_round_id를 구분해 남긴다. 과거 사례의 현재 회차 적용은 historical_transfer로 표시하고, 현재 공식 문항과 분량에 맞는지 검토한다. 다른 프로그램의 자료에는 cross_program 또는 general 범위를 적용한다. [작성 지원 명세](../../13_개인화_근거수집과_작성지원_명세.md), [기존 SQL의 회차와 근거 연결](../NVIDIA_2027-2028/schema.sql)

## 결과와 재현

[공고 전체 조회](../../../.local/batch-live-1k-20261109/runs/1bace1523bc71546c0fb965e14747dfa3f9c2d58a37a2bdd6e910b8793c5315b/output/rounds.result.json), [원본 계보가 포함된 선정 데이터](/home/longnew/.cache/datasets/benefitlens/live-students-20261109/cohorts/open-1000-selected.jsonl), [입력 파일과 해시](inputs.json)

저장소에서 아래 명령으로 저장된 당일 스냅샷을 다시 적재한다.

```bash
python3 tools/run_benefitlens_batch.py \
  --current-open-only --as-of 2026-11-09 \
  --state .local/batch-live-1k-20261109 \
  --incoming /home/longnew/.cache/datasets/benefitlens/live-students-20261109/cohorts/verified-open-1000
```

[Live 변환기](../../../tools/build_benefitlens_live_batch.py)는 입력 JSONL을 `--input`으로 여러 번 받아 원천별 선택, 원본 해시, 실제 지원 링크, 현재 상태와 근거 위치를 검증한 뒤 SQL 적재용 자료를 만든다. 원문 및 재사용 자료는 `/home/longnew/.cache/datasets/benefitlens/live-students-20261109`에 보존했다.

[작업 마일스톤](/home/longnew/Desktop/course_work/tmp/benefitlens_live_1k_20261109/milestones.md), [Claude 교차 검토](/home/longnew/Desktop/course_work/tmp/benefitlens_live_1k_20261109/claude_review.txt), [현재 상태 판별팀 기록](/home/longnew/Desktop/course_work/tmp/benefitlens_live_1k_20261109/quality/team_report.md)
