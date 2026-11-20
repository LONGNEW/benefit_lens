# 학생이 신청할 수 있는 활동과 지급 조건

Stipend는 라틴어 stipendium에서 왔다. Oxford는 stips를 “wages”, pendere를 “to pay”로 풀이한다. 현대 연구 연수의 stipend는 NIH 정책에서 연수 기간의 생활비 지급을 가리킨다. 검색은 원문에서 같은 목적의 지급을 다르게 부르는 표현과, 학생이 실제로 선택할 수 있는 인접한 지급 방식까지 확장한다. [Oxford](https://www.oxfordlearnersdictionaries.com/us/definition/english/stipend), [Lewis and Short](https://atlas.perseus.tufts.edu/dictionaries/entry/urn%3Acite2%3Ascaife-viewer%3Adictionary-entries.atlas_v1%3Alat.ls.perseus-eng2-n45427/), [NIH](https://www.grants.nih.gov/grants/policy/nihgps/HTML5/section_11/11.3.8_allowable_and_unallowable_costs.htm)

| 원문에서 찾을 표현 | 지급 조건 | 보관할 값 |
| --- | --- | --- |
| stipend, subsistence allowance, living allowance, 활동비, 연수수당 | 선발 후 활동 또는 재학을 이어가는 기간 | 주기, 지급 기간, 정액 또는 상한, 월별 활동 조건 |
| honorarium, participant compensation, 실험 사례비, 수료 수당 | 참여, 과제 제출 또는 수료 | 지급에 필요한 활동, 완료일, 기본액과 추가 보상 |
| paid internship, paid traineeship, 현장실습지원비 | 근무, 실습 또는 계약에 따른 활동 | 시간, 주당 소요, 고용과 실습 구분, 지급 단위 |
| prize money, cash prize, 공모전 상금 | 수상 또는 순위 | 전체 상금과 개인 또는 팀 지급액, 상금 형태 |
| project grant, seed grant, 학생 프로젝트비 | 채택된 과제 비용 집행 | 수령 주체, 사용 범위, 정산과 자부담 |
| travel reimbursement, tuition waiver, 실비, 등록금 감면 | 지출 증빙 또는 등록 | 증빙, 감면 대상, 한도, 직접 지급 여부 |

같은 공고에 월 활동비와 우수상 상금이 함께 있으면 지급 항목 두 개를 연결한다. 학교 자격은 특정 학교, 여러 학교, 학교 무관, 추가 조건 또는 미확인으로 보존한다. 공통 지원서의 자동 심사 기금들은 한 회차와 지원 경로로 집계한다. 개별 지원서는 현재 접수 중인 별도 신청 단계가 공식 원문에 있을 때 해당 모집에 연결한다.

| 담당 | 다음 인계에 담을 것 |
| --- | --- |
| 공고 수집 | 지급 표현과 활동을 조합해 원천을 찾고, 모집 주체, 회차, 지급 문구, 학생 자격, 공식 신청 경로와 접수 상태를 원문으로 확인한다. 기업과 대학이 같은 공고를 재게시하면 모집 주체의 원문으로 모은다. |
| 스키마와 개인화 | 원문 지급명, 지급 구분, 조건, 금액, 수령자, 소속 범위를 공통 필드 후보로 전달한다. 먼저 학교, 학위 또는 학년, 활동 가능 지역과 일정으로 좁히고 해당 공고에서만 필요한 항목을 추가한다. |
| 경험 수집 | 공고가 요구하는 제출물의 종류부터 확인한다. 학생이 공개한 실제 지원서, 참여 후기, 심사 기준을 자료별로 식별하고 같은 작성자의 여러 문서는 한 사례로 묶는다. |
| 작성 지원 | 공고에서 실제로 요청한 동기, 기획안, 포트폴리오 또는 연구계획서에 맞춰 지원한다. 사용자가 공개하지 않은 개인 정보는 해당 자료가 필요한 단계에서 용도를 밝히고 사용자에게 요청한다. |

학생증, 성적표, 신분증, 계좌 사본은 공고의 요구 여부와 제출 시점만 저장한다. 서비스 추천 단계에는 필요한 자격 값만 사용한다. 실험과 설문은 공식 모집에서 참여 요건, 시간, 보상, 신청 경로가 확인되는 건을 수집한다.

[검색 표현 설정](../../pipeline/config/dictionary_activity_support.json)은 후보를 발견하는 단계다. [인계 계약](team-handoff.md)에 원문 인용과 현재 모집 검증을 연결한다.
