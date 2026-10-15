# BenefitLens 저장 구조와 실행 경계

DB는 수집 근거, 발견 후보, 구조화 공고와 작성 지식을 구분한다. 같은 자료가 여러 계층에 등장하므로 테이블의 수를 합쳐 공고 개수로 보고하지 않는다.

기본 공개 데모는 이 구조에 공고 10개와 프로그램 지식 3개만 넣는다. `make sample-db`로 적재하며 전체 원장과 원문을 내려받지 않는다. 아래 계층은 전체 수집 환경까지 포함한 저장 설계다.

| 계층 | 테이블 또는 파일 | 역할 |
|---|---|---|
| 원천과 실행 | `platform.data_source`, `collection_run`, `quality_result` | 기관과 접근 경로, 수집 시도와 오류 |
| 기존 원문 | `platform.raw_document`, `document_version` | 원문 바이트, 해시와 버전 |
| 광역 발견 | `support_discovery_record`, `support_discovery_observation` | 후보와 발견 근거 |
| 전처리 작업 | `pipeline_work_item` | 원문 확인과 정규화 대기 상태 |
| 확장 원문 | `pipeline_document`, `pipeline/data/*/raw`, `text` | 파일 경로, 해시와 추출 텍스트 |
| 새 공고 모델 | `opportunities`, `opportunity_revision` | 공고와 프로그램 ID, 회차, 자격 트리, 혜택과 근거 |
| 경험 탐색 | `experience_discovery_case` | 사람, 프로그램과 회차로 구별한 사례 후보 |
| 작성 지식 | `program_knowledge` | 프로그램별 사례 배열, 작성 패턴과 근거 |
| 검색 상태 | `exploration_url`, `exploration_search_batch`, SQLite | 응답, 방문 상태, 사전과 단계 종료 조건 |
| 기존 웹 모델 | `opportunity`, `opportunity_version`, `eligibility_rule` 등 | 기존 정본과 D1 연계용 데이터 |

위 제품 테이블은 `benefitlens` 스키마에 있다.

## 실제 화면은 어느 DB를 읽는가

현재 데모는 Python `data_platform.pipeline_demo`가 `opportunities`와 `program_knowledge`를 `program_id`로 연결한다. 읽기 전용 SQL로 조회하며 원문 파일을 HTTP로 공개하지 않는다. 원문 출처 URL은 근거와 함께 전달한다.

`web/app/`의 Next/Vinext 앱은 별도 D1 동기화 경로다. PostgreSQL 복원만으로 D1에 반영되지는 않는다. 새 팀 환경의 기본 실행은 Python 데모이며 D1 배포는 별도 작업으로 둔다.

## 어떤 레코드가 조건 비교에 쓰이는가

광역 원장은 지원 정보를 포함할 수 있는 후보다. `opportunities`에도 원천 필드만 옮긴 레코드와 원문을 읽어 조건을 구성한 레코드가 섞여 있다. `extractor`, `extraction_status`, `resolution_status`와 근거를 함께 확인한다.

경험 후보는 `experience_discovery_case`에, 작성 패턴까지 검토한 자료는 `program_knowledge`에 들어간다. 후보 354개를 검토된 지원서 354개로 표현하지 않는다.

## 현재 파이프라인

1. 검색 사전을 확장하고 공식 공고, 첨부와 경험 자료의 URL을 발견한다.
2. 크롤링과 공개 API로 원문을 보존하고 텍스트를 추출한다. 스캔에는 OCR을 적용한다.
3. 프로그램과 회차를 식별하고 자격, 혜택과 제출 요구사항을 추출한다.
4. 출처와 원문 인용을 연결해 스키마에 저장하고 검토 상태를 기록한다.
5. 사용자의 기본정보를 저장된 자격 트리와 비교한다.
6. 선택한 프로그램의 사례와 작성 패턴을 불러와 초안을 준비한다.

현재 검색 호출에는 에이전트가 기록한 검색 응답이 필요하다. 크롤러 실행만으로 새 검색어의 웹 검색이 자동 수행되지는 않는다. LLM 전처리, 경험 패턴 정리와 반복 탐색의 완전 무인화는 남은 작업이다.

## 팀 관리 기준

공통 스키마는 `pipeline/migrations/`, `pipeline/schemas/`를, 원천 등록은 `pipeline/config/`를 변경한다. 수집 실패와 미해석 조건은 상태로 보존한다. 갱신과 종료 정책은 `proposal/중간발표/04_DB_갱신과_보존_삭제.md`에서 관리한다.

현재 DB는 기존 모델과 새 모델을 함께 보존한다. 이관을 완료하기 전까지 이름이 비슷하다는 이유로 테이블을 삭제하거나 합치지 않는다.
