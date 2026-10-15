# BenefitLens

흩어진 장학금과 지원 프로그램을 수집해 개인 자격과 혜택으로 검색하고, 공개 지원 경험의 작성 패턴을 연결하는 데이터 엔지니어링 프로젝트다.

## 바로 실행

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

[약 64KB의 공개 표본](data/sample/programs.json)으로 다음 내용을 재현한다.

- 국내와 해외 공고 및 제도 10개
- 자격 조건 트리, 혜택, 기간, 요구사항과 공식 출처
- 3개 프로그램의 경험 요약 9건과 작성 패턴
- 가상 프로필 비교, 공고 선택과 빈칸 개요

기준일은 2026년 10월 15일이다. 과거 회차와 일부 공통 조건을 보여주는 예제이며, 현재 접수 가능성과 전체 자격을 보증하지 않는다. 지원서 전문, 개인 프로필과 원문 파일은 포함하지 않는다.

예제별 범위는 [표본 안내](data/sample/README.md), 저장 관계는 [DB 구조](docs/DATA_LAYOUT.md)를 따른다.

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

전체 수집 데이터는 로컬에 별도로 보관한다. 이 데모에는 약 33GB 백업이 필요 없다. [재현과 백업](docs/REPRODUCING.md)에서 보관 경계를 설명한다.
