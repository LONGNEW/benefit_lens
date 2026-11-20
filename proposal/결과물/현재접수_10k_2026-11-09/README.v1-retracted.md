# BenefitLens 현재 접수 공고 10k 결과

2026년 11월 9일 한국시간 11:50~14:04의 공식 원천을 수집했다. 교차 검토에서 기간과 상태가 충돌한 15건을 보류하고, 남은 10,718행의 재수집 중복 247행을 합쳤다. **고유 공고 10,471건에서 학생 대상과 수집 시점의 접수 근거가 있는 10,000건을 선정했다.** 최종 검증 시각은 2026-11-09T14:19:34+09:00다.

[SQL 검증](verification.json), [독립 집계](audit.json), [최종 공통 형식 공고 조회](</home/longnew/Desktop/course_work/26-2 학기/데이터엔지니어링/benefit_lens/.local/batch-live-10k-20261109/runs/3f5febb6e1dafdfc350c2edded4b55d53cb3af9820ad2bced792fb1d9aeef95f/output/rounds.result.json>), [공식 원문 예시](examples.md)

| 공통 유형 | 최종 공고 |
| --- | ---: |
| 인턴십 | 9,088 |
| 장학금 | 241 |
| 박사과정 지원 | 17 |
| 기타 학생 근로, 논문 활동과 미분류 | 654 |
| 합계 | 10,000 |

94개 원천의 고유 ID와 공식 상세 URL이 각각10,000개다. 신청 진입 URL은9,799개로 일부 장학금과 과정이 양식을 공유한다. 근무지, 언어, 첨부 문서나 모집 자리 수를 따로 늘려 세지 않았다.

| 주요 원천 | 최종 공고 |
| --- | ---: |
| workday/cvshealth | 1,838 |
| walgreens | 870 |
| workday/thales | 687 |
| BoschGroup/SmartRecruiters | 676 |
| workday/walmart | 655 |
| workday/ag | 433 |
| Amazon.jobs | 419 |
| Mercedes-Benz/jobs.api.mercedes-benz.com | 321 |
| siemens | 304 |
| BMWGroup/bmwgroup.jobs | 249 |
| SchneiderElectric/Jibe | 221 |
| ESA | 175 |

약국 관련 제목의 공고가3,302개이고, 가장 큰 issuer의 비중은18.38%, 상위10개는55.04%다. 단순 공백과 대소문자를 정리한 제목은6,141종이다. 같은 프로그램의 매장별 실제 채용 공고도 포함되며, 프로그램군 전체의 고유 개수는 아직 확정하지 않았다. [전체 원천 및 issuer 분포](audit.json)

공식 현재 목록과 상세의 학생 조건, 공개 신청 링크를 연결했다. Workday는 canApply와 posted, SmartRecruiters는 active와 visibility, Schneider는 applyable과 searchable를 원본 JSON에서 확인했다. HTML 원천은 당일 목록, 본문 기간과 실제 anchor를 대조했다. 마감 날짜와 정확한 인용 위치가 확인된 공고는2,936개, 날짜 미공개 또는 미파싱은7,064개다. 이 중410개는 별도 게시 종료일이 있으며 신청 마감으로 저장하지 않았다.

API가 접수 가능으로 남아 있어도 본문에 지난 인턴 기간이 있거나, 본문과 헤더의 마감 및 입학 회차가 충돌하면 보류했다. 15건은 원문과 근거를 유지한 ambiguous_held이며, 종료가 확정됐다는 상태로 바꾸지 않았다. [보류 목록](quarantine.json), [정확 원문과 위치 1](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/schema/final_past_cohort_quarantine.json), [정확 원문과 위치 2](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/schema/final_past_cohort_quarantine_additional.json), [장학금 및 연구기관 점검](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/quality/final_body_date_qa_report.md)

Walmart는 공식 목록670개에서 학생 근거663개를 확인하고, 같은 ID의 공개 페이지에서 OPEN과 실제 자리 수 양수를 확인한655개를 남겼다. 자리 수0인8개는 보류했다. 예전 수집 파일로 이 공고들이 다시 들어오는 반례를 재현해, Walmart에는 별도 공식 페이지 근거를 필수화했다. [독립 코드 검토](schema_review.md)

공통 ID, 회차, 날짜, 상태와 신청 URL은 SQL에 저장하고, 원천별 조건은 claim.value의 JSONB와 evidence로 연결한다. 새 NoSQL DB는 도입하지 않았다. 원천의 doctoral_studentship은 admission_funding으로 변환한다. 유형이 생략된 공고는 공식 제목의 명시된 인턴 및 학생 근로 표현으로 분류하고, 근거가 부족한120개는 other로 보존했다. [유형 정규화 영수증](type_normalization.json)

PostgreSQL16.15에서 공고10,000개, 원문 문서10,710개, 주장20,094개와 근거24,089개를 적재했다. 원본 및 텍스트 해시, 모든 인용 위치와 FK 계약을 검사했고 위반0개, 같은 입력 재적재 결과 동일을 확인했다. 실제 SQL DISTINCT(provider,native)도10,000개다. 회귀 테스트19개가 통과했다. 검증은 네트워크와 공개 포트가 없는 임시 DB에서 실행했고, 운영 DB에는 쓰지 않았다. [실제 SQL 집계문](identity_audit.sql)

현재 검증 범위는 학생 대상과 접수 상태다. 국적, 비자, 학점, 가용 기간과 학교별 조건을 모두 충족하는 개인 매칭은 아직 완료되지 않았으며 full_eligibility_verified는 false다. 대학 유급 학생 근로50개에는 급여 근거, 소속 학교 제한, 근로장학 조건과 내부 신청 경로도 보존했다. [조건 표현 30개 예시](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/schema/eligibility_samples_30.jsonl)

과거 경험은 별도 패킷으로 관리했다. Paul Bergen의 회고와 대학 수상 기록, Nicolas Papernot의 공식 인터뷰로 사례2개를 추가해 기존 자료와 합계9개가 됐다. 기존 작성 패턴6개를 유지했고, 새 제출 지원서 전문과 새 승인 패턴은0개다. 별도 SQL 검증도 위반0개 및 재적재 동일을 확인했다. [경험 검증](historical_verification.json), [경험 원문 감사](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/quality/experience_audit_report.md), [과거 경험 조회](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/root/historical-sql/experiences.result.json)

팀별 인계 기록은 [수집팀](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/discovery/team_report.md), [Schneider 추가 수집](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/discovery/phase2/team_report.md), [스키마 및 기업팀](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/schema/team_report.md), [Sydney](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/quality/sydney_report.md), [Cambridge](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/quality/cambridge_report.md), [원문 최종 점검](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/quality/final_body_date_qa_report.md)에 있다. 인계본에서 교차 검토 및 최종 선정으로 제외한 자료는 위 최종 집계에 반영했다.

저장소에서 아래 명령으로 고정 입력을 검증하고 적재 결과를 재현한다.

```bash
python3 tools/run_benefitlens_batch.py \
  --current-open-only --as-of 2026-11-09 \
  --state .local/batch-live-10k-20261109 \
  --incoming /home/longnew/.cache/datasets/benefitlens/live-students-10k-20261109/cohorts/verified-open-10000-v2
```

[입력 파일 및 SHA](inputs.json), [원문 계보가 있는 선정 스냅샷](/home/longnew/.cache/datasets/benefitlens/live-students-10k-20261109/cohorts/open-10000-selected.jsonl), [실행 경로](artifacts.json), [작업 마일스톤](/home/longnew/Desktop/course_work/tmp/benefitlens_live_10k_20261109/milestones.md), [Claude 검토 반영](review_disposition.md)
