# BenefitLens 예제 재현과 전체 데이터 보관

## 기본 배포는 공고 10개

Git의 표본과 SQL만으로 실행한다.

```bash
make init
make install
make db-up
make sample-db
make smoke
make demo
```

로더는 체크섬, 고유 공고 10개와 프로그램별 사례 연결을 확인한다. 빈 DB에는 스키마를 먼저 구성한다. 같은 표본은 재적재하고 다른 공고나 원장이 있는 DB는 변경하지 않는다.

DB 포트는 55433, 기본 실행 화면은 5180이다. 현재 로컬 서비스 5179도 이 예제 DB를 사용한다. 공개 표본에는 원문 전문과 실제 개인 정보를 포함하지 않는다.

## 단계별 실행 증거까지 다시 만들려면

기존 원문과 `.local/pattern-runs/` 캐시 및 Node가 있을 때 `make pipeline-replay`를 실행한다. 다른 경로의 수집 기록은 `COLLECTION="/private/path/collection.json"`으로 지정한다. 이 실행은 원문 29개의 해시와 경험 인용을 재검증하고 공고 10개를 두 번 적재한 뒤 임시 HTTP 서버로 11개 시나리오를 검사한다. 임시 서버는 검사 후 종료한다.

성공 기록은 `.local/pipeline-replay/latest.json`과 `web/pipeline-demo/pipeline-run.json`이다. 실행 중인 데모의 `/assets/pipeline-run.html`에서 읽는다. 원문 다운로드, 신규 모델 호출과 광역 수집 재개는 하지 않는다. 원문이나 모델 캐시가 없으면 이 경로는 실패하며 기본 표본 데모는 그대로 사용할 수 있다.

## 저장한 경험 원문을 다시 분석하려면

신규 공고의 광역 수집은 중지했다. 사용자 요청에 따른 지정 경험 URL만 `make experience-originals COLLECTION="기존 collection.json의 절대 경로"`로 추가 확보할 수 있다. 결과 원문과 합친 수집 기록은 `.local/experience-originals/`에 저장한다. `make patterns COLLECTION=".local/experience-originals/collection.json"`로 합친 수집 기록 `.local/experience-originals/collection.json`의 원문 전체를 LLM에 전달하고 `make sample-db`로 결과를 적재한다. Codex CLI 로그인과 로컬 원문 파일이 필요하다. 입력 해시가 같으면 검증한 결과를 재사용한다. 새 원문을 연결한 뒤 분석하지 않은 표본은 DB 로더가 거부한다. `pipeline/config/student_demo_scope.json`이 예제 10개와 수집 중지 상태를 정한다.

## 전체 백업이 필요한 경우만

전체 묶음은 PostgreSQL 덤프, 스키마, 테이블별 행 수, 원문 압축과 파일별 SHA256을 포함한다. SQLite는 online backup으로 캡처한다. PG, SQLite와 파일의 캡처 시점은 각각 다르다.

```bash
make verify BUNDLE="/private/path/to/full-bundle"
make restore BUNDLE="/private/path/to/full-bundle"
```

전체 복원은 별도 빈 DB와 비어 있는 데이터 폴더에서 한다. 공고 10개를 넣은 DB에 덮어쓰지 않는다. 복원 도구는 기존 테이블이나 원문을 삭제하지 않는다.
