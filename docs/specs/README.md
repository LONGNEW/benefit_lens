# 실행 인계와 개인화 경로의 제안 API

2026-11-09 세 팀의 로컬 인계 조정기와 수집 및 작성 어댑터를 구현했다. 입력 검증, 승인된 값의 SQL 투영과 임시 PostgreSQL 실행 범위는 [팀 인계 계약](team-handoff.md)을 따른다. 아래 개인화 POST API는 설계 제안으로 남아 있다.

원형을 복사하지 않고 새 현재 공고와 공통 표현, 과거 경험을 연결하는 후속 구현은 [증분 확장](expansion.md)을 따른다.
학생이 참여하거나 성과를 내면 지급되는 기회까지 찾는 수집 범위는 [활동과 지급 조건](activity-support.md)을 따른다.

현재 구현되지 않은 API다. 아래 경로는 [개인화 명세](../../proposal/13_개인화_근거수집과_작성지원_명세.md)를 실제 개발 작업으로 나누기 위한 계약이며, 기존 GET API를 교체하지 않았다.

## 입력과 출력

| 제안 경로 | 요청의 핵심 값 | 응답의 핵심 값 |
| --- | --- | --- |
| POST /api/personalization/plan | opportunity_id, 현재 profile, 보유 document_ids | requirement 목록, 필요한 필드와 시점, 자료 확보 계획과 질문 |
| POST /api/personalization/documents | 사용자 파일, 자료 역할과 대상 주체 | evidence 스냅샷, 추출 후보와 실패 상태 |
| POST /api/personalization/enrich | plan_id, 사용자가 지정한 공개 검색 단서와 출처 범위 | 동일인 연결 후보, 원문 근거와 사실 후보, 미확보 출처 |
| POST /api/personalization/confirm | plan_id, fact_id, 확인 값과 거절 또는 충돌 사유 | 확인 응답을 근거로 만든 새 사실과 이전 후보의 계보 |
| POST /api/personalization/resources | opportunity_id, round_id, 요구 서류 ID | 공식 양식과 안내, 개인 준비 자료의 확보 상태와 경로 |
| POST /api/personalization/draft | plan_id, prompt_id, 사용할 fact_ids와 guidance_ids | 초안 슬롯과 문장, 사용한 사실, 빈칸과 추가 질문 |

외부 공개 검색에는 사용자 이름과 소속, 제공한 프로필 URL이나 ORCID처럼 검색에 필요한 공개 식별 단서만 보낸다. 업로드 문서 전체나 비공개 사실을 검색 질의로 변환하지 않는다. 요청 주체와 문서의 주체 연결을 서버 측 세션에서 확인하는 어댑터가 필요하다.

## 응답 수준

응답은 status, plan과 errors로 구성하는 안이다. status는 ready, partial, needs_user_input, failed다. plan의 구조는 [personalization-plan.schema.json](personalization-plan.schema.json)을 따른다. 원문과 입력 사실 및 규칙 버전으로 plan_id를 구분하고 확인 뒤에는 새 버전을 남긴다.

자료가 일부 없으면 partial 또는 needs_user_input으로 반환하고, 이미 확보한 원문과 사실 및 미확인 요건을 유지한다. errors에는 code, requirement_id 또는 fact_id, reason과 next_action을 둔다.

오류 코드는 identity_ambiguous, source_unavailable, official_document_not_found, inconsistent_evidence, unsupported_requirement, unaccepted_fact_in_draft 등을 제안한다. 실패를 공고 없음이나 조건 불충족으로 바꾸지 않는다.

## 저장과 읽기

제안 저장 단위는 개인별 사실 스냅샷, 공고 요구별 자료 확보 상태, 입력 버전별 판정과 작성 슬롯이다. 개인 문서의 원문은 공개 공고 표본과 분리한다. 현재 테이블과의 구체적인 마이그레이션은 구현 시 검토하며 이 문서 작성으로 테이블을 만들지 않았다.

JSON Schema는 필드와 상태의 계약이다. [합성 검사기](../../evaluation/spec_examples/validate_contract.py)는 교차 참조와 주체 및 시점의 선언된 일관성도 검사한다. 실제 동일인 판별, 문서 진위, 인용의 의미와 자격 계산의 정확도는 검증하지 않는다.

구현 순서는 plan → documents/enrich → resources → confirm → draft다. 외부 검색 공급자와 실제 업로드/다운로드 어댑터는 아직 연결하지 않았다.
