# 신청 기간을 다시 검증한 결과

집계 단위 정정: 아래 4,339건은 기간 근거를 확인한 원천 모집 게시물 수다. 같은 기업과 같은 직무를 묶으면 현재 규칙상 1,129개 잠정 그룹이 되고 58개는 추가 검토가 필요하다. [중복과 지원 분류 재점검](../공고_중복과지원분류_2026-11-09/README.md)을 함께 읽는다.

기존의 “현재 접수 가능한 1만 건 확보” 보고를 철회했다. 공식 게시 상태와 학생 대상 근거가 있는 자료를 모았다는 사실만으로, 신청 기간까지 확인된 공고 수에 포함한 것이 오류였다.

## 다시 세면 몇 건인가

2026년 11월 9일 수집한 후보 10,471건을 세 팀이 재검토했고, 통합 검증 코드로 판정을 재검사했다. 같은 원천과 공고 ID가 같으면 한 건으로 셌다.

| 판정 | 기존 선택 10,000건 | 전체 후보 10,471건 |
|---|---:|---:|
| 신청 기간 근거 확인 | 3,869 | 4,339 |
| 신청 기간 근거 없음 | 5,987 | 5,987 |
| 날짜 해석 또는 기한 의미 미확정 | 99 | 99 |
| 날짜 충돌, 인재풀 등 보류 | 45 | 46 |
| 합계 | 10,000 | 10,471 |

당시 선택하지 않았던 후보에서 470건이 추가로 통과했다. 기간 검증 원천 모집건 4,339건은 **마감일 근거 확인 3,326건, 명시적 상시 접수 1,013건**으로 구성된다. 원천 모집건 수로도 1만 건에 미달하며, 고유 기회 수는 새 중복 기준으로 별도 집계한다.

수집본의 확인 시각은 한국 시간 11:50:32부터 14:04:15까지다. 특정 학생이 국적, 학위, 비자와 자격 조건을 모두 충족하는지는 아직 검증하지 않았다.

## 어떤 근거로 남기거나 제외했나

- [Walgreens 공고](https://jobs.walgreens.com/en/job/erie/pharmacy-intern/1242/100006350864)의 “This role will remain open until filled”는 실제 자리의 접수 종료 조건을 명시하므로 상시 접수로 인정했다.
- [Schneider 공고](https://careers.se.com/jobs/117259?lang=en-us)의 “This position will be posted until filled”는 게시 상태에 관한 문구여서 신청 기간 근거로 승격하지 않았다.
- [ABB 공고](https://abb.wd3.myworkdayjobs.com/External_Career_Page/job/Ostrava-Moravskoslezsk-kraj-Czechia/Student-Program---Diplomov-a-bakalsk-prce_JR00032251)는 “This role is part of our talent pipeline, which means it’s not currently open”이라고 밝혀 보류했다.
- [Bosch 공고](https://jobs.smartrecruiters.com/BoschGroup/744000159365257-undergraduate-placement-year-2027-bps-lean-operations-planning-intern)의 “Deadline for Applications: 01/12/2026”은 숫자는 읽을 수 있지만 일과 월의 순서를 입증하지 못해 해석 미확정으로 남겼다.

Workday의 endDate만 있던 15건도 제외했다. 통과한 포털 날짜는 공식 신청 잔여 기간 표시와 사람이 읽는 종료일을 함께 대조했다. 본문과 포털 날짜가 충돌하는 10건, 현재 열린 자리가 아닌 인재풀 14건, 당일 마감 시간 미확인 19건, 지난 마감 1건과 추가 날짜 충돌 2건은 보류했다.

해석 미확정 99건에는 최소 게시 또는 접수 보장 기간 45건, 의미를 입증하지 못한 endDate 15건, 날짜를 정규화하지 못한 신청 기간 단서 12건, 신청자에게 적용되는 기한인지 불분명한 IAESTE 날짜 26건, Bosch의 모호한 숫자 날짜 1건이 포함된다.

## 코드와 DB에서 바꾼 것

수집 입력에 신청 기간 판정, 공식 원문, 원문 해시, 정확한 인용과 줄 위치를 요구했다. 기간이 미확정이면 확보 배치를 생성하지 않는다. 상시 접수는 원문의 명시 문구를 요구하며, rolling review와 게시 종료 문구를 구분한다. 게시일을 신청 시작일로 사용했던 ESA 자료도 수정했다.

SQL에는 `verified_application_period` 근거를 별도로 저장한다. 현재 공고 적재 경로도 이 근거를 재검증하므로 이전 후보 파일을 그대로 넣어 현재 확보 수로 세는 경로가 차단된다.

PostgreSQL 16.15에 4,339건을 적재해 기간 근거 누락 0건, 날짜 불일치 0건, 계약 위반 0건을 확인했다. 동일 입력 재적재로 행이 늘지 않았다. 원문 해시와 25,434개 인용 위치를 검사했고, 회귀 검사 33개를 통과했다. 운영 DB는 변경하지 않았다.

## 수량과 다양성은 별도다

4,339건 중 인턴십은 4,010건이다. CVS Health 2,308건과 Walgreens 870건이 합쳐 73.2%를 차지한다. 서로 다른 지역과 원천 공고 ID로 구분한 기록 수이며, 앞으로 수집 범위의 다양성을 평가할 때 이 편중을 함께 봐야 한다. 과거 경험 사례와 작성 패턴 수는 이번 현재 공고 수에 합치지 않았다.

검증 파일: [집계와 판정](audit.json), [SQL 검증](verification.json), [배치 생성 검사](build.json), [고정 파일 경로와 코드 해시](artifacts.json).

담당 보고: [수집팀](</home/longnew/Desktop/course_work/tmp/benefitlens_strict_10k_20261109/discovery/team_report.md>), [스키마팀](</home/longnew/Desktop/course_work/tmp/benefitlens_strict_10k_20261109/schema/team_report.md>), [기간 검토팀](</home/longnew/Desktop/course_work/tmp/benefitlens_strict_10k_20261109/quality/team_report.md>). 기간 검토팀의 474건 고정본에서 당일 마감 시간 미확인 1건을 통합 검증 단계에서 추가 보류해 473건을 채택했다.
