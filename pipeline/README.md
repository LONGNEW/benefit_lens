# BenefitLens 데이터 파이프라인

새 팀 환경에서는 루트 [빠른 실행](../README.md)과 [데이터 복원](../docs/REPRODUCING.md)을 먼저 따른다. 아래 출처별 설명과 D1 흐름은 기존 구현 기록이다. 현재 대규모 원장과 Python 데모는 [저장 구조](../docs/DATA_LAYOUT.md)를 따른다. 새 환경에서는 루트 `compose.yaml`을 사용한다.

공식 기관의 지원 기회를 수집하고 원본, 버전, 품질 검사 결과를 PostgreSQL에 보존한 뒤 웹용 D1 조회 모델로 복제한다. 검색 결과는 발견 보조 수단으로만 사용하고, 공식 API와 공식 페이지를 정본으로 삼는다.

## 현재 범위

- 학교: POSTECH 공통 학부 장학제도, 대학원 재정지원과 공식 학과 목록 전체를 확인해 발견한 공개 지원 페이지
- 외부 공고: POSTECH CSE 전달 공고, 포스코청암재단과 한국장학재단 목록 및 상세
- 계획: 미래에셋, 일주, 삼성꿈, SK행복나눔, 현대차 정몽구, 한국장학재단 월간 데이터, 온통청년 API
- 기본 상태: 사람 검토 전 `quarantined`

## 구성

- `config/benefitlens_sources.json`: 학교, 재단, 기업, 국가 수집 원천과 권리 상태
- `src/data_platform/`: 수집, 파싱, 품질 검사, PostgreSQL 적재
- `migrations/`: BenefitLens 전용 스키마, 표준 조건 사전과 판정 기록 구조
- `scripts/sync_d1.py`: PostgreSQL 후보 데이터를 웹 D1으로 복제
- `tests/`: 파서와 스키마 계약 테스트
- `data/exports/`: 수집 요약 산출물

## 로컬 실행

### 통합 색인과 검색 후보로 시작하는 탐색 수집

`config/discovery_demo.json`은 공개 CSV에서 선택할 기관과 검색 서비스에서 얻은 공식 후보 URL, 검색어와 방문 한도를 지정한다. `data_platform.discovery`는 두 경로를 같은 대기 목록으로 합치고 관련 링크를 따라간다. 현재 CLI는 검색 후보를 입력받으며 검색 서비스 호출 자체는 내장하지 않았다.

```bash
PYTHONPATH=src python3 -m data_platform.discovery --output data/discovery/2026-10-13-demo --demo-output ../web/public/discovery-collection.json
PYTHONPATH=src python3 -m pytest tests/test_discovery.py tests/test_http.py -q
```

출력은 실행별 JSON, `collection.sqlite`와 저장이 허용되는 경로의 원문 파일이다. SQLite의 `catalog_item`, `document_version`, `discovery_edge`와 `discovery_run`은 색인, 원문 버전, 발견 관계와 실행을 연결한다. 같은 버전은 재사용하고 실행별 발견 관계는 보존한다. 이 저장소는 새 출처 탐색의 준비 단계이며 기존 PostgreSQL과 D1의 지원 공고를 자동 승격하지 않는다.

깊이는 2, 실제 페이지 HTTP 요청은 실행당 32회와 호스트당 8회로 제한한다. CSV 다운로드, 제한 확인과 리디렉션도 요청 예산에 포함하고 robots.txt는 별도 확인한다. robots 정책과 로그인 경로를 요청 전에 검사한다. 외부 호스트는 후보로 남기고 허용 호스트 등록 전에는 방문하지 않는다.

경험 자료, 경험 목록과 선정자 소개는 메타데이터만 저장한다. 인터뷰가 포함된 일주재단 첫 화면도 동일하게 처리한다. 이 자료의 원문 파일과 본문 해시는 남기지 않고 메타데이터 해시를 구분한다. 공식 공고와 안내의 내부 원본은 공개하지 않으며 자료 이용 검토는 유지한다.

수집 화면은 `/benefitlens/collection`이다. 고유 원문, 메타데이터와 미등록 도메인을 구분하고 CSV 행 또는 검색어, 시작 URL, 이전 페이지와 깊이를 확인할 수 있다. CSV 확보가 실패하거나 원문을 하나도 확보하지 못한 실행은 기존 웹 산출물을 덮어쓰지 않는다.

### 추가 검색 주소의 원문 확보와 후보 적재

100개 검색의 `summary.json`을 `original_collection`에 넣으면 검색 주소, 전달 공지, 발행기관 본문과 첨부의 연결 기록을 만든다. `config/original_sources.json`의 발행기관 관계를 확인한 자료만 공고 후보로 전처리한다. 다른 학교의 공지는 발견 경로로 보관한다. HTML, PDF, HWP, HWPX, 이미지 OCR과 ZIP 내부 Word 및 PowerPoint 텍스트를 추출한다. PowerPoint의 텍스트 순서는 참고 자료로 남기며 자격 문장에 바로 적용하지 않는다.

```bash
PYTHONPATH=src python3 -m data_platform.original_collection --input data/keyword_search/2026-10-13/summary.json --output data/original_ingestion/2026-10-13
PYTHONPATH=src python3 -m data_platform.original_collection --input data/keyword_search/2026-10-13/summary.json --output data/original_ingestion/2026-10-13 --refresh-snapshot
PYTHONPATH=src python3 -m data_platform.ingest_originals --collection data/original_ingestion/2026-10-13/collection.json --output data/original_ingestion/2026-10-13/ingestion.json --public-output ../web/public/original-ingestion.json
```

완료한 주소 처리 기록만 적재한다. 원문 바이트는 PostgreSQL에 해시 검증 후 보존하며 공고 후보에는 조건 구역과 첨부 근거를 연결한다. 공고와 정책, FAQ, 입학요강의 장학금 언급과 경험 자료를 구분한다. 같은 게시글의 표시 옵션과 정책 페이지의 탭 별칭은 합치고 사업 ID 및 모집 회차는 유지한다. 이번 탐색에서 잘못 승격한 참고 자료는 후보 목록에서 비활성화하되 원문과 과거 버전을 보존한다. 기존 등록 공고는 유지하며 신규 후보는 `needs_review`다. 반복 적재는 같은 원문 및 공고 버전을 재사용한다.

웹 빌드가 끝난 뒤 `scripts/sync_local_preview.py --node <Node 절대 경로>`로 후보 스냅샷을 로컬 D1에 반영한다. `scripts/verify_original_preview.py --manifest <ingestion.json> --public-manifest <web/public/original-ingestion.json>`은 PostgreSQL과 로컬 조회 API의 전체 ID 집합이 일치할 때만 웹 동기화 완료를 기록한다. `/benefitlens/collection`은 주소별 원문 링크, 적재 후보와 미확보 상태를 보여준다. 원문 전문은 웹 실행 기록에 포함하지 않는다.

### 기존 등록 출처의 갱신과 PostgreSQL 적재

```bash
docker compose -p benefitlens up -d postgres
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e '.[test]'
python3 -m data_platform.cli migrate
python3 -m data_platform.cli collect-benefitlens
python3 -m data_platform.cli classify-benefitlens
python3 -m data_platform.cli export-summary
pytest
```

정규식과 출처별 파서를 기본으로 실행한다. 지원 문항 후보와 OCR 텍스트의 의미 분류까지 로컬 Codex로 보조하려면 로그인된 Codex CLI가 있는 환경에서 옵션을 켠다.

```bash
codex --version
python3 -m data_platform.cli migrate
python3 -m data_platform.cli collect-benefitlens --codex
```

로컬 `codex exec`는 읽기 전용 샌드박스와 임시 세션에서 실행되며 최종 응답을 JSON Schema로 제한한다. 지원 문항 탐색 대상인 모집 공고와 OCR 원천만 분석하고, 구조화된 지원제도 안내는 OCR 원천이 없으면 생략한다. OCR 원천은 추출 텍스트와 PostgreSQL에 저장된 원본 이미지를 함께 입력한다. 원문 인용이 실제 HTML, PDF 또는 OCR 텍스트와 일치하는 후보만 `benefitlens.extraction_candidate`에 `pending`으로 적재한다. 후보는 정본 공고 버전, 공개 뷰와 자동 자격 판정을 변경하지 않는다. 기본 한도는 실행당 공고 100건, 배치당 공고 20건, 공고당 입력 12,000자, 배치당 OCR 이미지 4개이며 `config/benefitlens_sources.json`에서 조정한다. `--llm`은 기존 실행 스크립트를 위한 `--codex` 별칭이다.

기존 로컬 데이터 볼륨을 보존하기 위해 물리 DB 이름과 역할은 `unlock`을 유지하고, 제품 테이블은 `benefitlens` 스키마를 사용한다. 기본 연결 문자열은 `postgresql://unlock:unlock_local_dev@127.0.0.1:55432/unlock`이다. 다른 DB를 쓰려면 `DATABASE_URL`을 지정한다.

55432 포트가 사용 중이면 다음처럼 변경한다.

```bash
BENEFITLENS_POSTGRES_PORT=55433 docker compose -p benefitlens up -d postgres
export DATABASE_URL=postgresql://unlock:unlock_local_dev@127.0.0.1:55433/unlock
```

온통청년 API는 현재 비활성 계획 출처다. 활성화할 때만 `ONTONG_YOUTH_API_KEY`를 실행 세션에 주입한다. 키가 없으면 기존 후보를 지우지 않고 해당 실행만 차단한다.

자격 조건은 `eligibility-1.6.0`의 36개 기준으로 정규화한다. 학년 범위, GPA, TOPIK과 거주 개월도 추출한다. GPA의 만점, 평가 기준과 지정 기간은 `eligibility_rule.context_json`에서 웹 규칙의 `context`로 연결한다. 원문, 값, 단위와 AND 및 OR 관계를 보존하며 미해석 조건이 남으면 `partial` 또는 `review_required`로 둔다.

상세 공고에 연결된 공식 페이지, 첨부 PDF, HWP, HWPX와 포스터를 추가 수집한다. PDF는 `pdftotext`, HWP는 설치된 `hwp5txt`, HWPX는 제한된 ZIP과 XML 파서를 사용한다. 이미지 OCR은 `BENEFITLENS_OCR_COMMAND`, 시스템 tesseract, `config/document_tools.json` 순으로 한국어와 영어 모델을 갖춘 실행환경을 찾는다. 로컬 설정의 절대 경로는 다른 환경에서 갱신해야 한다. 추출 실패는 원본과 오류로 보존하고, OCR 자료는 이미지 대조 검토를 요구한다. 링크는 허용 호스트, 공개 HTTPS와 응답 크기 제한을 적용하며 리디렉션도 접근 전에 검증한다.

`evidence_sources_json`에는 연결 URL, 추출 텍스트, 해시, 수집 상태와 원본 문서 ID를 저장한다. 웹에는 전문과 내부 ID를 제외한 출처 상태 및 규칙별 근거 URL을 전달한다. 수집 실패, 자동 해석 미완료와 개인 미입력은 다른 상태다. 출생연도만 아는 경우 가능한 두 만 나이가 모두 조건을 만족할 때만 통과한다. 군필 연령 예외는 복무 개월과 함께 계산한다. 접수 가능은 공개 기본 자격과 공고 기간의 충족이며 최종 선발이나 잔여 정원을 보장하지 않는다.

공고 버전에 `record_kind`와 `presentation_json`을 저장한다. `policy`는 지원제도 안내, `notice`는 모집 공고다. 내부 표시 원문을 역할별로 정리해 웹에는 지원 자격, 제외, 우대, 요구사항, 선발과 심사, 혜택, 특약, 접수 기간과 활동 기간의 9개 구역을 전달한다. 자격 계산은 별도 조건 트리를 사용한다. 모집 기간이 없는 교내 안내는 접수 가능으로 승격하지 않는다.

학교 탐색은 공식 학과와 대학원 목록에서 공개 HTML, PDF와 HWPX 다운로드를 확인한다. 학과당 최대 6페이지, 링크 깊이 2로 제한한다. 발견 실패와 접근 실패는 `data/exports/school_coverage.json`에 별도로 기록하며, 미발견을 지원 없음으로 해석하지 않는다. TLS 검증을 끄거나 로그인을 우회하지 않는다.
학과 사이트의 과거 제도 주소와 현행 제도 주소가 함께 노출되면 URL 별칭을 현행 주소로 정규화한다. 과거 원문과 버전은 감사 이력으로 보존하고 최신 버전만 공개 후보에 남긴다.

## 검증 증거

`export-summary`는 출처별 최신 품질 검사, 수집 이력, 현재 공고 수를 `data/exports/collection_summary.json`에 남긴다. `normalized_field_coverage`는 원문 필드의 존재 비율, `condition_normalization_coverage`는 규칙과 미해석 상태를 별도로 검사한다. 같은 현재 내용을 다시 처리할 때 버전이 늘지 않아야 한다. 정상, 실패, 정상으로 변하면 마지막 정상 내용에 새 유효 구간을 만들고 과거 버전을 보존한다.

## 명시적 검토

```bash
PYTHONPATH=src python3 -m data_platform.cli review-packet --version <현재-공고-버전-ID> --output <검토-파일.json>
PYTHONPATH=src python3 -m data_platform.cli review-benefitlens --packet <검토-파일.json> --reviewer <검토자>
```

검토 파일에 `promote`, `reject` 또는 `reviewed` 결정과 사유를 입력한다. 기본 명령은 검증만 수행하며 저장하려면 `--apply`를 명시한다. 정본 승격은 원문 인용, 현재 버전과 기준 변환을 검증한 뒤 새 버전과 `review_event`를 만든다. 출처 권리와 운영 허용은 별도이며 이 명령으로 자동 승인하지 않는다.

목록 첫 페이지에서 밀려난 기존 미마감 공고도 상세 수집 한도 내에서 다시 확인한다. 상세 요청이 실패하면 이전 정상 상세 버전을 목록 제목만 있는 버전으로 덮어쓰지 않는다. 연결 자료의 실패는 목록 상세 수집률과 별도 품질 항목으로 기록한다.

## 금전 지원 중심 분류

`funding-1.0.0`은 제목, 추출한 혜택과 `presentation.benefits`에서 현금성 지원, 장학금, 등록금과 생활비를 복수 분류한다. 자격, 접수 기간과 페이지의 나머지 본문은 지급 근거로 사용하지 않는다. 제목의 장학 표현만 확인되면 장학 유형만 부여하고 금액이나 용도를 생성하지 않는다.

무료 교육, 채용 안내, 물품만 제공하는 사업, 상금만 있는 대회, 대출과 대여 장학금은 기본 금전 지원 목록에서 제외한다. 분류 근거가 없는 후보는 `unconfirmed`로 남기며 전체 보관 목록에서 조회할 수 있다.

`benefitlens.funding_classification`에는 공고 버전 ID, 분류기 버전, 복수 유형, 근거 문구, 근거 필드와 처리 시각을 저장한다. 신규 수집 및 기존 버전 재확인 때 자동 적용한다. 분류 규칙을 바꾼 뒤 `classify-benefitlens`를 실행하면 저장된 최신 후보를 재분류할 수 있다. D1에 동기화한 `funding_tags_json`과 `funding_evidence_json`으로 검색하고 근거를 표시한다. 운영기관과 수집 경로는 독립 필드이며 비영리 협회는 기업으로 분류하지 않는다.

## D1 동기화

웹과 실행 셸의 `D1_SYNC_TOKEN` 값이 같아야 한다. 동기화는 스테이징 적재, 건수 검증, 서비스 테이블 교체 순서로 진행된다.

```bash
read -rsp 'D1 동기화 토큰: ' D1_SYNC_TOKEN
export D1_SYNC_TOKEN
PYTHONPATH=src python3 scripts/sync_d1.py --base-url https://<배포 URL>
unset D1_SYNC_TOKEN
```

## 데이터 공개 원칙

- 공식 원본을 먼저 저장하고 파싱 결과는 기본적으로 `needs_review`로 둔다.
- 수집 실패 시 이전 정상 버전을 삭제하지 않는다.
- 권리 상태가 승인되고 사람이 검토한 항목만 공개 뷰에 포함한다.
- 사용자 조건은 브라우저에서 비교하며 서버 수집 데이터와 분리한다.
- 지원서 데이터는 공식 문항, 동의받은 사용자 경험과 합성 테스트 자료를 우선하며 권리가 불명확한 합격 사례 전문은 저장하지 않는다.

PostgreSQL 원본 백업이 필요하면 다음 명령을 사용한다.

```bash
docker exec benefitlens-postgres pg_dump -U unlock -d unlock -Fc > benefitlens.dump
```
