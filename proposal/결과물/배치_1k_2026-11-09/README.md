# BenefitLens 1천 건 수집 및 적재 결과

2026년 11월 9일 후속 작업에서 [현재 접수 공고 1천 건](../현재접수_1k_2026-11-09/README.md)을 별도로 확정했다. 이 파일은 과거 기록이 포함된 이전 배치의 실행 기록이다.

2026년 11월 9일 한국시간 기준, **공식 원천에 연결된 학생 개인 대상 공고 1,000건**을 별도 PostgreSQL에 적재했다. 공고 한 건의 단위는 독립된 모집 게시물 또는 프로그램의 한 회차다. 기존 요약 6건은 보존해 전체 조회 행은 1,006건이다.

| 출처 | 건수 | 확인한 내용 |
| --- | ---: | --- |
| 기존 공식 원문 연결 공고 | 11 | 기존 장학금, 펠로십, 개인 연구지원 및 인턴십 회차를 보존했다. |
| USAJOBS 과거 학생 채용 | 984 | 학생 경로와 Student Trainee 직함을 확인했다. 원천의 임용 유형이 `Internships`인 568건과 기타 학생 채용 416건을 구분했다. |
| CERN 학생 인턴십 | 5 | 접수 중으로 표시됐다. 서로 다른 공고 ID, 재학 조건, 마감과 지원 링크를 확인했다. 단기 인턴 분야별 공고 3건과 여름 학생 프로그램 2건이다. |

[USAJOBS Historic JOA API](https://developer.usajobs.gov/api-reference/get-api-historicjoa)에서 직군 0099, 0399, 0899의 첫 페이지를 받았다. 응답 1,500행에서 학생 채용 경로가 없던 164행, 학생 Trainee 범위 밖 196행, 취소 108행, 과거 공고 날짜 조건에 맞지 않은 4행, 중복 5행을 제외했다. 남은 1,023개 중 게시일이 최근인 984개를 적재했다. USAJOBS 공고의 실제 게시 연도는 2022~2024년이다. 공고 ID와 기관별 공고번호를 대조해 재게시와 여러 근무지 표기를 중복 집계하지 않았다. [수신 페이지와 원본 해시](usajobs_pages.json)

CERN은 공식 공용 상세에서 재학 조건과 공개 접수 상태를 확인했다. 실제 지원 양식 내부 항목은 미확인이다. [CERN 공식 목록](https://careers.cern/jobs/), [개별 공고와 검증 기록](cern_verified.json)

## 검증 범위

- PostgreSQL 16.15에서 동일 입력을 재적재한 결과가 같았고, DB 무결성 위반은 0건이었다. 원본 해시 48개, 추출문 해시 49개, 근거 위치 1,119개가 일치했다. [SQL 검증](verification.json)
- USAJOBS 984건 각각을 원본 배열 위치의 고유 공고번호, 기관 코드, 직함과 학생 경로에 다시 연결했다. 개별 공식 API 링크는 목록 전 구간에서 고른 5건으로 응답과 공고번호를 확인했다. [전수 대조](audit.json), [개별 공식 링크 검사](usajobs_link_check.json)
- 현재 상태는 원문 연결 1,000건 중 접수 중 6건, 마감 6건, 과거 기록 984건, 접수 예정 1건, 미확인 3건이다. USAJOBS 쪽은 학생 대상 채용 분류만 확인했다. 학위, 국적, 지원 서류 등 세부 요건은 전체 배치에서 미확인이다. 상세 API는 [과거 공고 1건](https://data.usajobs.gov/api/historicjoa/announcementtext?USAJOBSControlNumbers=758449700)에서 별도로 확인했다.
- 신규 공고에 쓸 경험 사례와 작성 패턴의 증거는 확보하지 않았다. 기존 사례 7건과 작성 패턴 6건을 같은 조회 결과로 보존했다. 학생 대상 필드 대조와 SQL 검증은 출처 연결의 검증 범위다.

## 이번에 수집 경로를 바꾼 이유

이전 검색 실행은 텍스트 추출 호출 한도에 도달해 8개 청크를 대기 상태로 남겼다. 이번에는 공식 구조화 목록을 한 번에 읽어 공고 행을 뽑고, 상세 검증이 필요한 CERN은 개별 상세를 확인했다. 따라서 이번 1천 건은 USAJOBS 한 원천이 98.4%를 차지한다. 기관과 지원 종류의 다양성, 접수 중 공고 수는 다음 규모에서도 건수와 별도로 집계할 기준이다. [이전 배치 기록](../배치확대_2026-11-09/README.md), [Claude 교차 검토](/home/longnew/Desktop/course_work/tmp/benefitlens_scale_1k_20261109/claude_review.md)

## 재현

저장소에서 실행한다. 공식 응답과 파생 텍스트는 사용자 공용 캐시에 있다. 적재는 격리된 PostgreSQL에서 검증한 뒤 조회 결과와 검증 기록을 남긴다.

```bash
report=proposal/결과물/배치_1k_2026-11-09
dataset=/home/longnew/.cache/datasets/benefitlens/usajobs/historic-joa-student-20261109

python3 tools/build_benefitlens_usajobs_batch.py \
  --manifest "$report/usajobs_pages.json" --count 984 \
  --output "$dataset/approved-v1-984"

python3 tools/run_benefitlens_batch.py \
  --state .local/batch-run-1k-20261109 --as-of 2026-11-09 \
  --incoming "$dataset/approved-v1-984" --incoming "$report/cern-approved"
```

[공고 전체 조회 결과](../../../.local/batch-run-1k-20261109/runs/1b65aaf5e46e233baff64f7dc3873e0e3fe669875c152cfcb209e0b920825b2f/output/rounds.result.json), [실행 체크포인트](../../../.local/batch-run-1k-20261109/latest.json), [작업 기록](/home/longnew/Desktop/course_work/tmp/benefitlens_scale_1k_20261109/milestones.md)

[수집팀 기록](/home/longnew/Desktop/course_work/tmp/benefitlens_scale_1k_20261109/discovery/delivery_report.md), [스키마팀 기록](/home/longnew/Desktop/course_work/tmp/benefitlens_scale_1k_20261109/schema/team_report.md), [품질팀 기록](/home/longnew/Desktop/course_work/tmp/benefitlens_scale_1k_20261109/quality/team_report.md)
