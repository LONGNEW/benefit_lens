# 세 팀의 실행 가능한 인계 계약

기준일: 2026-11-09. 기존에 저장한 원문으로 수집 → 공통 표현 → 작성 준비를 연결하는 로컬 실행 계약이다. 공개 데모의 기존 GET API와 개인화 POST API 제안은 별도로 유지한다.

새 출처와 검토 내용을 원형에 덧붙이는 후속 실행은 [증분 확장 명세](expansion.md)를 따른다. 아래 예제 수치는 첫 인계 구현의 검증 범위다.

## 무엇을 넘기는가

수집팀은 프로그램, 모집 회차, 문서 스냅샷, 원문 인용, 추출 후보를 넘긴다. 스키마팀은 공통 필드와 프로그램별 확장을 구분하고 후보별 승인 기록을 붙인다. 작성팀은 승인된 공식 문항과 사례 자료로 작성 슬롯, 사용할 근거, 부족한 사용자 정보를 반환한다.

| 계약 단위 | 핵심 내용 |
| --- | --- |
| program, opportunity_round | 기관의 프로그램과 원천 모집 변형, 확인 기준일 |
| source_document | URL, 접근 범위 선언, 원문 및 텍스트 경로와 해시, 불변 document_id |
| evidence | 프로그램과 회차, 문서 ID, 실제 원문 인용과 LF 줄 위치 |
| claim | kind, field, value, 적용 시점 at, evidence_ids, semantic_review |
| experience_case, case_document | 사례 인물, 결과, 자료 역할과 최종 제출 여부, 문서 연결 |
| writing_pattern, pattern_evidence | 대상 문항의 작성 관찰과 사례 근거 |
| metadata | 부모 후보 연결, 원천별 확장, 의존 조건, 작성 결과와 감사 기록 |

JSON 구조는 [team-handoff.schema.json](../../pipeline/schemas/team-handoff.schema.json), 교차 참조와 원문 검증은 [benefitlens_handoff.py](../../tools/benefitlens_handoff.py)를 따른다. document_id는 URL이 아닌 문서 스냅샷의 ID다. 동일 ID에 다른 URL이나 해시를 재사용하면 같은 실행 상태 디렉터리에서 거절한다.

## 공통 표현과 나머지 정보

SQL의 10개 테이블과 기존 적재 순서를 유지한다. 조회 키와 연결은 관계형 열로, 조건과 프로그램별 내용은 JSON 값으로 저장한다. 이번 작업에서 MongoDB나 별도 운영 부모 테이블을 만들지 않았다. 부모 후보 연결과 상세 승인 기록은 인계 JSON에 보존한다.

[common-fields.schema.json](../../pipeline/schemas/common-fields.schema.json)에 아래 여섯 필드를 등록했다. 공통 필드로 등록되지 않은 값은 확장으로 남긴다.

| 필드 | 보존할 구분 |
| --- | --- |
| enrollment_maintenance | 활동 기간 중 재학 유지, 전일제 여부 |
| concurrent_funding_rule | 다른 지원과 중복 수혜 조건 |
| application_route | 직접 지원이나 기관 추천 경로 |
| document_requirement | 서류 종류, 제출 주체와 시점, 예외 검토 상태 |
| support_component | 학비와 생활비 등 용도, 지급 방식, 금액 종류와 주기 |
| funding_gap_rule | 충당할 비용, 적용 대상, 허용 재원과 자비 부담 가능 여부 |

이번 실제 예제는 네 필드에 대한 매핑 7개다. 중복 수혜와 지원 경로의 전체 자료 적용은 남아 있다. 금액은 exact, range, cap, percentage, unknown을 구분한다. `$`만 있으면 통화 코드는 null로 두며, 비어 있는 예외 목록에도 not_reviewed를 명시한다.

예를 들어 CERN의 원문 “You remain enrolled as a full-time student during your studentship.”는 현재 재학 사실과 구분해 활동 기간 중 유지 조건으로 저장했다. Cambridge의 “Student self-funding of this difference is not permitted.”는 국제학생 학비 차액의 충당 규칙으로 연결했다. 두 인용의 원문 경로, 해시와 전체 문맥은 실행 입력과 review receipt를 통해 추적한다.

## 승인과 보류

semantic_review.state는 accepted, candidate, ambiguous, conflict, rejected다. 판단 근거는 agent_semantic_review, human_semantic_review, existing_rule_review, machine_rule로 구분한다. 검토자와 이유, 결정 ID와 검토 기록 경로를 남긴다. 에이전트 검토를 사람 검토로 표시하지 않는다.

승인은 claim 값, 적용 프로그램과 회차, 원문 스냅샷과 인용, 검토 정책의 지문에 결속한다. 이 입력이 바뀌면 기존 승인은 보류된다. machine_rule은 student_scope, current_application_status, verified_application_period 세 필드에만 허용한다. 승인 기록의 존재는 문서 진위나 해석의 독립적인 정확도 측정을 대신하지 않는다.

의존 조건은 metadata.claim_dependencies의 claim ID 목록으로 선언한다. 선행 조건이 보류되면 종속 claim과 해당 패턴도 보류한다. 누락 참조, 다른 모집 변형의 조건 연결과 순환은 오류다. 선언되지 않은 의존성을 자동 추론하는 기능은 없다.

원문 파일의 해시, 정확한 인용과 줄 위치를 검증한 뒤 승인된 claim과 패턴만 SQL 적재용으로 투영한다. 후보, 보류 이유와 원천 확장은 인계 JSON이나 고정 입력 참조에 남는다. 투영 전후의 승인 ID와 claim-evidence 연결 집합도 대조한다.

## 작성 준비의 출력

[writing_patterns.py](../../pipeline/src/data_platform/writing_patterns.py)는 경험 문서 전체 텍스트를 LLM 입력으로 구성한다. 새 v3 컨텍스트는 자료 역할, 회차와 최종 제출 여부를 보존하며 비교할 사례가 없으면 no_comparable_pair를 허용한다. 길이 한도를 넘으면 실패를 반환한다. 자동 분할은 미구현이다.

기존 v2 캐시는 원래 버전으로 재검증하고 legacy_v2_requires_scope_review로 남긴다. 캐시 재사용을 새 v3 추출이나 의미 승인으로 기록하지 않는다.

[benefitlens_writing_handoff.py](../../tools/benefitlens_writing_handoff.py)는 승인된 실제 작성 문항에 필요한 사용자 사실을 연결한다. 기관 추천 절차나 서류 목록을 에세이 문항으로 바꾸지 않는다. 사실은 같은 사람과 같은 경험의 근거에 묶여야 한다. 정보가 없으면 draft_text는 null이고 추가 질문을 반환한다. 이 어댑터는 모델을 호출하지 않으며 완성 초안을 생성하지 않는다.

NVIDIA의 보존된 과거 회차 예제에서는 사례 3개와 패턴 2개를 연결했다. 사용자 사실이 없어 연구 질문, 본인 기여, 결과와 비교 기준, 다음 검증, 자원 활용에 관한 질문 5개를 반환했다. 공개 인터뷰와 과거 문장을 최종 제출문이나 합격 원인으로 단정하지 않는다.

## 실행과 재현

조정기는 [run_benefitlens_handoff.py](../../tools/run_benefitlens_handoff.py)다. 기존 배치 디렉터리는 --packet, 고정 인계 파일은 --bundle로 받는다. --decisions는 정확한 base 지문에 맞는 승인 패치이며 claim 값과 원문 연결은 바꾸지 않는다.

```bash
python3 tools/run_benefitlens_handoff.py \
  --bundle /absolute/path/handoff.json \
  --state /absolute/path/state --database
```

--enrichments와 --reviewer, --review-receipt로 명시적으로 검토한 공통 매핑을 추가한다. --required-fields는 문항별 필요한 사용자 사실, --facts는 확보한 사용자 사실이다. 전체 대형 입력에는 --check-only로 원문과 투영을 검사하면서 기존 고정본을 참조할 수 있다. --check-only와 --database는 함께 쓸 수 없다.

각 실행은 입력, 코드와 스키마 지문별로 고정 결과를 보존한다. 같은 입력을 다시 실행할 때 원문을 재검증하고 저장된 결과의 해시를 대조한다. --database는 기존 이미지를 사용하는 네트워크 없는 임시 PostgreSQL에 적재하고 같은 입력 재적재와 계약 위반 0건을 확인한다. 운영 DB 변경은 없다.

이번 실행은 신규 HTTP 수집과 데이터 추출 모델 호출이 없다. 정기 갱신 스케줄러, 웹 API와 화면 연결, 전체 지원 유형 정규화, 개인별 자격 전수 판정과 완성 초안 생성은 후속 구현이다. 실행별 정확한 집계와 경로는 [현재 상태](../PROJECT_STATUS.md)의 결과 보고를 따른다.
