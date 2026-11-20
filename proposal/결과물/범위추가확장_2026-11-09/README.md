# 범위 추가 확장 결과, 2026-11-09

직전 검증본을 보존하고 공식 원문에서 접수 중인 모집 변형 **114개**를 추가했다. 전체는 **4,492개**, 같은 기관과 역할을 묶은 부모 후보는 **1,275개**다. 부모 후보 중 규칙 통과 1,214개와 검토 대상 61개를 구분한다. 의미적으로 전수 검수한 고유 기회 수는 아직 확정하지 않았다.

## 수집팀: 어디까지 넓혔는가

| 신규 원천 | 검증 통과 모집 변형 |
| --- | ---: |
| EURAXESS | 22 |
| ASIA-OFFICIAL-FUNDING | 15 |
| JOBBNORGE | 77 |
| 합계 | 114 |

유럽은 급여형 박사과정과 학위 훈련에 연결되는 연구학생 모집을 추가했다. EURAXESS 공고의 활동 국가는 Austria, Belgium, Denmark, Finland, France, Germany, Italy, Netherlands, Portugal, Spain, Sweden, United Kingdom이다. 출처 플랫폼, 실제 고용기관, 활동 국가와 신청 경로를 별도로 보존한다. 공식 이메일 접수는 원문의 안내 섹션과 실제 주소를 연결한다.

아시아는 Singapore, Japan, Korea와 Hong Kong, Taiwan의 공식 공개 자료를 포함한다. NPGS, HKUST 장학제도, Amgen 연구 방문, KOSAF의 명명 기부 장학금과 보림 장학금을 확보했다. 게시 지역은 지원자의 국적이나 허용 국적을 뜻하지 않는다.

같은 NPGS의 일반 및 NIE 경로, HKUST의 입학 전형별 경로와 Amgen의 대학별 경로는 같은 제도 아래의 모집 변형으로 저장했다. 원문 하나에 서로 다른 기부자와 지원 조건의 장학금이 있으면 명명된 제도를 구분했다. 정원과 수혜 인원은 공고 수에 더하지 않았다.

EURAXESS에서 HTTP429가 발생한 뒤 그 호스트에 대한 수집을 중단했다. WAF, 빈 본문, 인증서 검증 실패와 기간 불명 자료는 현재 확보 수에서 제외했다. 수집기가 공개 목록에서 발견한 총량은 검증 통과 공고 수에 합산하지 않았다.

## 스키마팀: 무엇을 공통 표현으로 만들었는가

아시아 15개 모집 변형과 11개 제도를 인계했다. 원문 10개를 검증하고 학비 지원, 활동 지원금과 생활비 등의 **공통 지원 항목 6개**를 추가 승인했다. 기존 원형과 기존 승인 claim은 유지했다.

실제 원문은 다음과 같이 구분한다.

- NPGS: “This award also covers the annual tuition fee.” 금액을 제시하지 않은 학비 지원으로 기록했다. [공식 페이지](https://www.ntu.edu.sg/admissions/graduate/financialmatters/scholarships/npgs), [보존 본문](/home/longnew/.cache/datasets/benefitlens/expansion2-20261109/schema/36c3433b7c2d60a9f058-337ea8230480.txt:527)
- Tokyo Amgen: “A stipend of JPY 280,000 will be provided”를 해당 대학 경로의 지원금으로 기록했다. [공식 페이지](https://www.u-tokyo.ac.jp/en/prospective-students/amgen_program.html), [보존 본문](/home/longnew/.cache/datasets/benefitlens/expansion2-20261109/schema/8d281abc1f6cf8e68ec6-9bda2ac059aa.txt:150)
- KOSAF: “1인 당 생활비 장학금 200만 원”을 생활비 지원으로 기록했다. [공식 페이지](https://www.karts.ac.kr/nri/bbs/NuriBbsDetail.do?bbsId=BBSNURI_001&menuType=&nttNo=9856776&pageIndex=4), [보존 본문](/home/longnew/.cache/datasets/benefitlens/expansion2-20261109/schema/83f0103820dd03a7acdb-be39d7d9717e.txt:177)
- 보림의 최대 총액은 학기별 분할과 후속 조건을 동반하므로 즉시 지급 확정액으로 바꾸지 않았다.

각 원문의 파일, LF 위치와 정확한 인용은 [공통화 인계 기록](/home/longnew/Desktop/course_work/tmp/benefitlens_expansion2_20261109/schema/execution_receipt.json)과 그 안의 semantic_patch에 있다. 지원자별 세부 자격과 모든 지급 방식의 공통화는 남아 있다. 확인하지 못한 통화 코드와 표현할 수 없는 지급 주기는 unknown 또는 null로 두고 원문 조건을 보존했다.

한국어 날짜는 명시적 연월일, 점 표기와 같은 연도 접수 구간을 지원한다. “2026년 10월 22일(목) 9시 ~ 11월 12일(목) 18시”의 끝 날짜는 2026-11-12다. 독립된 “11월 12일”이나 수정일에서 마감 연도를 만들지 않는다.

Jobbnorge의 deadlineOn은 공식 화면의 신청 마감 라벨인 Søknadsfrist와 실제 DOM 바인딩을 대조한다. API 요청의 jobId와 JSON id, 원천 ID를 교차 검사한다. 다른 공고의 날짜, 게시 종료 필드와 바뀐 화면 연결은 거절한다. 학생성은 제목행을 빼고 실제 모집 역할과 현재 학위훈련 문맥으로 검사한다. 행정지원과 완료 학위를 입학으로 읽는 반례, 다른 공고의 본문을 섞는 반례를 거절하고 잘린 인용 5개를 복원했다. [독립 재검사](/home/longnew/Desktop/course_work/tmp/benefitlens_expansion2_20261109/schema/review_jobbnorge_v3.json)에서 원문 77개의 인용 재현을 확인했다. HTTP 원문 확보 시각과 텍스트 파생 시각도 분리했다.

## 작성팀: 무엇을 더 확보했는가

Chevening과 Rhodes Canada의 개인 전체 원문 **3명**을 추가해 통합 사례는 **8건**, 승인된 한정 지침은 **7개**가 됐다. 신규 문서 9개는 전체 원문 8개와 공개 검색 발췌 1개다. UCL 발췌는 개인 사례와 승인 패턴 입력에서 제외했다.

세 개인 자료는 회고나 인터뷰이며 실제 제출문이 아니다. 신규 승인 지침 3개는 본인의 행동과 결과, 이전 서술과 달라진 계획, 공동체 활동에서의 책임을 묻는 준비 질문이다. 비교 가능한 선정 및 미선정 쌍은 추가하지 않았다.

Chevening 경험 원문 2개 전체 13,083자를 LLM으로 무절단 분석하는 1회를 실제 실행했다. 모델 후보 6개는 [별도 결과](/home/longnew/Desktop/course_work/tmp/benefitlens_expansion2_20261109/writing/llm_candidates.json)에 남겼고 승인 지침에 자동 추가하지 않았다. 현재 사용자 질문은 통합 21개, 빈 작성 슬롯은 5개다.

공식 AI 작성 제한을 구조화하고 두 새 프로그램의 제출문 생성 콜백이 차단되는 것을 통합 검사했다. 실제 사용자 사실을 사용한 완성 지원서 생성은 0개다. 제품의 일반 생성 경로에 대한 연결은 후속 작업이다.

## 통합 검사와 재현

전체 원형과 증분의 문서 8,596개, 인용 26,280개에 대한 원문 해시와 위치 검증을 통과했다. SQL 실행은 신규 프로그램과 모든 작성 프로그램을 포함한 별도 표본으로 한정했다.

| SQL 검증 표본 | 행 수 |
| --- | ---: |
| 모집 회차 | 119 |
| 승인 claim | 360 |
| 경험 사례 | 8 |
| 작성 지침 | 7 |

격리된 PostgreSQL의 계약 위반은 0행이고 같은 입력 재적재 결과도 같았다. 전체 인계와 SQL 표본을 재실행하여 캐시 적중 및 receipt 해시 보존을 확인했다. 회귀 검사 199개가 통과했다. 운영 DB 쓰기와 대용량 원형 복사는 수행하지 않았다.

이번 집계는 이전 manifest의 ID 집합과 비교했다. 이전 4,378개만 재조합하면 신규 0개이고, 이전 추가 39개를 이번 신규로 다시 세지 않는다. 확인 기준일이 다른 manifest와의 단순 비교나 이전 행의 누락은 거절한다.

- [검증 결과](validation.json)
- [고정 manifest](/home/longnew/.cache/datasets/benefitlens/expansion2-20261109/integration/manifest-dd1f9177c351088c.json)
- [재현 실행 스크립트](/home/longnew/Desktop/course_work/tmp/benefitlens_expansion2_20261109/root/run_integration.py)
- [단계별 진행 기록](/home/longnew/Desktop/course_work/tmp/benefitlens_expansion2_20261109/MILESTONES.md)

수집 증분과 공통 스키마, 작성 준비가 연결된 로컬 검증 결과다. 일일 스케줄, 제품 조회와 사용자별 전체 자격 판정은 아직 연결하지 않았다. 기존 대출 후보 2개는 학업 시작일에 따른 상대 마감 검증이 남아 현재 공고 수에 더하지 않았다.
