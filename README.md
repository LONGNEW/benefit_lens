# BenefitLens

목표: 내게 맞는 지원 기회부터, 나를 드러내는 신청 준비까지.

2026-11-20 문단 전술은 [서사형 템플릿과 작성 예시](docs/WRITING_NARRATIVE_V3.txt), [빈칸형 양식 3종](docs/templates/빈칸형_작성템플릿_v3.txt)을 따른다. 상황에서 문제와 요구를 도출하고, 각 과제의 본인 기여와 범위가 남은 질문과 다음 연구를 잇도록 구성한다. 과제별로 완료, 진행, 계획을 구분하며 공식 문항의 순서와 분량을 우선한다.

[전체 프로젝트의 현재 가치와 실행 계획](docs/PROJECT_FRAMING_CURRENT.txt)은 기회 발견, 개인 적합성 확인, 부족 정보 보완, 공식 서류와 초기 초안 준비, 변경 시 재검토를 연결한다. 경험 분석과 글쓰기 전술은 신청 준비 단계의 기능이다. 2026-11-20의 16문서 분석과 59개 기술 테스트는 해당 작성 묶음의 실적으로 구분한다.

학생 개인 대상 지원 기회의 자격과 혜택을 비교하고, 경험 원문과 글쓰기 근거를 분석해 개인 작성 지원으로 연결하는 데이터 엔지니어링 프로젝트다. 현재 작업 기준은 대량 수집분 전체의 조건 정규화, 경험 분석과 개인화 연결이다. 2026-11-19 DB 확인에서 공고 후보는 36,856건, 경험 사례는 29,955건이다. 후보 수와 현재 모집 검증, 의미 검토 완료 수는 구분한다.

[대량 프로젝트의 팀 연결과 처리 기준](docs/PRODUCTION_ORGANIZATION_V2.txt), [5팀 실행 계약](pipeline/config/team_production_v2.json)을 현재 기준으로 사용한다. 공고팀과 개인화팀은 [같은 조건 사전](pipeline/config/shared_condition_catalog_v1.json)을 소비하고, 코드와 계약 검사는 운영팀이 맡는다. 아래 10개 예제와 과거 증분 검증은 보존된 재현 경로다.

2026-11-20부터 [경량 일괄 처리](docs/specs/compact-processing-v1.txt)를 사용한다. 코드가 기존 추출문을 문장과 관련 표현으로 나누고 동일 문맥을 묶는다. Codex 서브 에이전트의 최초 80묶음 검토를 거쳐, 현재는 50개씩 남은 전체 큐를 실제 Codex CLI로 처리한다. 최신 경험 705사례도 추가했다. [전량 처리와 팀 확장](docs/specs/full-corpus-and-team-expansion-v1.txt)에 실행과 범위를 기록한다. 이전 문서별 장문 worker와 Claude 자동 재시도는 중단 상태다. 실제 처리량은 공용 캐시의 `compact/status.json`, `compact/handoff/summary.json`, 팀 확장의 `operations/team-refresh-status.json`을 따른다.

## 보존 데모 재현

Python 3.11 이상, pip 22.3 이상, Docker Compose, Git과 Make가 필요하다. 기본 데모에는 Node, API 키, LLM 로그인과 대용량 데이터 다운로드가 필요 없다.

```bash
git clone https://github.com/LONGNEW/benefit_lens.git
cd benefit_lens
make init
make install
make db-up
make sample-db
make demo
```

브라우저에서 **http://127.0.0.1:5180/benefitlens**를 연다. `make sample-db`가 빈 DB에 스키마와 예제를 넣는다. 반복해도 공고가 중복되지 않으며 기존 전체 DB에는 적재를 거부한다.

## 포함된 예제

[학생 예제 표본](data/sample/programs.json)으로 다음 내용을 재현한다.

- 학생 또는 진학 예정 개인 대상 국내외 공고 및 제도 10개
- 자격 조건 트리, 혜택, 기간, 요구사항과 공식 출처
- 3개 프로그램의 경험 사례 14건과 전체 경험 문서 25개를 LLM으로 분석한 작성 패턴
- 가상 프로필 비교, 공고 선택과 빈칸 개요

기준일은 2026년 10월 15일이다. 과거 회차와 일부 공통 조건을 보여주는 예제이며, 현재 접수 가능성과 전체 자격을 보증하지 않는다. 지원서 전문, 개인 프로필과 원문 파일은 포함하지 않는다.

일반 기관 연구비와 교원 전용 사업은 예제에서 제외한다. 대학 추천이 필요한 학생 장학금은 포함한다. 보존 예제의 범위는 [학생 예제 10개](proposal/현재_범위_학생개인_예제10개.md)를 따른다.

예제별 범위는 [표본 안내](data/sample/README.md), 저장 관계는 [DB 구조](docs/DATA_LAYOUT.md)를 따른다. [Writing pattern 원문과 예제](docs/WRITING_PATTERN_EXAMPLES.md)에서 실제 모델 입력 문서 25개와 추출 결과를 볼 수 있다.

## 시스템 명세와 단계별 데모

[시스템 파이프라인 명세](proposal/중간발표/00_시스템_파이프라인_명세.md)는 미술 큐레이팅 사례의 발표 순서에 맞춰 수집, 두 전처리, DB와 추론을 연결한다. 기존 네 문서와 [API 및 검증 명세](proposal/중간발표/05_추론_API와_데모_검증.md)에 입력, 출력과 통과 기준을 둔다.

`/assets/pipeline-run.html`에서 마지막 재실행 결과와 공고 10개의 판정, 경험 패턴 및 작성 개요를 확인한다. 이 화면의 기록은 원문을 다시 검색하거나 LLM을 새로 실행한 결과가 아니다.

기존 비공개 원문과 모델 캐시, Node, 예제 PostgreSQL이 있으면 다음 명령으로 전체 재검증 경로를 실행한다.

```bash
make pipeline-replay
```

원본 텍스트 재추출과 해시 검사 → 공고 저장 계약 검사 → 경험 근거 재검증 → DB 두 번 적재 → 실제 HTTP 조회와 웹 판정 함수 → 빈칸 개요 순서다. 현재 6개 공고의 원문 연결, 전체 공고의 자동 LLM 추출, 개인화 문장 생성과 갱신 배치는 남아 있다.


## 경험 원문에서 패턴을 다시 추출하기

기본 데모는 저장된 결과를 사용한다. 기존에 확보한 원문의 `collection.json`과 로그인된 Codex CLI가 있을 때만 전처리를 다시 실행한다. 추가 검색과 다운로드는 하지 않는다.

```bash
make experience-originals COLLECTION="/private/path/to/existing/collection.json"
make patterns COLLECTION=".local/experience-originals/collection.json"
make sample-db
```

`writing_patterns.py`는 프로그램별 경험의 전체 텍스트를 모델에 전달한다. 문서와 사례별 분석, 평가 기준에 대한 본인 행동과 결과 근거, 선정 및 미선정 글의 관찰 비교를 만든다. 모든 인용과 작성자 연결을 검사하고 입력 해시와 모델을 기록한다. 입력 한도를 넘으면 자르지 않고 중단한다. 경험이 없는 예제에는 패턴을 만들지 않는다.

## 프로젝트 구성

| 폴더 | 내용 |
|---|---|
| `proposal/중간발표/` | 수집, 공고 전처리, 경험 전처리, DB 관리 발표 문서 4개 |
| `pipeline/src/`, `config/`, `schemas/` | 수집과 전처리 코드, 출처와 공통 스키마 |
| `pipeline/migrations/` | 빈 PostgreSQL부터 구성하는 SQL |
| `data/sample/` | 작은 예제 데이터와 체크섬 |
| `web/pipeline-demo/` | 현재 PostgreSQL 데모 화면 |
| `tools/` | 예제 적재, 환경 관리와 선택적 전체 백업 도구 |

기존 Next/Vinext와 D1 코드는 별도 구현이며 기본 실행에는 사용하지 않는다. 과거 웹 수집 JSON과 검증 원문은 비공개 로컬 보관 자료다.

## 확인 명령

```bash
make status
make smoke
make test
```

`make smoke`는 실제 PostgreSQL API를 검사하고 임시 서버를 종료한다. 전체 Python 테스트에는 선택적 백업 도구 검증용 `zstd`가 추가로 필요하다. CI는 빈 DB에 예제를 두 번 적재하고 API를 확인한다.

과거 수집 데이터는 로컬에 별도로 보관하며 활성 API는 선택한 학생 예제 10개만 조회한다. 기본 데모에는 전체 백업이 필요 없다. [재현과 백업](docs/REPRODUCING.md)에서 보관 경계를 설명한다.
