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

DB 포트는 55433, 화면은 5180이다. 표본 JSON은 약 64KB이며 원문 전문과 실제 개인 정보를 포함하지 않는다.

## 원문을 다시 수집하려면

공식 출처와 구조화 결과는 `data/sample/programs.json`에서 확인한다. 실제 크롤링은 `pipeline/src/data_platform/`의 해당 수집기를 사용한다. PDF에는 Poppler, OCR에는 한국어와 영어 모델이 설치된 Tesseract가 필요하다.

수집 결과는 `pipeline/data/`에 저장하고 Git에 넣지 않는다. 공개 표본에 반영할 때 조건의 범위, 배포 권리와 개인 정보를 검토한다. `tools/build_sample.py`는 전체 로컬 스냅샷에서 표본을 만드는 유지보수용 도구다.

## 전체 백업이 필요한 경우만

전체 묶음은 PostgreSQL 덤프, 스키마, 테이블별 행 수, 원문 압축과 파일별 SHA256을 포함한다. SQLite는 online backup으로 캡처한다. PG, SQLite와 파일의 캡처 시점은 각각 다르다.

```bash
make verify BUNDLE="/private/path/to/full-bundle"
make restore BUNDLE="/private/path/to/full-bundle"
```

전체 복원은 별도 빈 DB와 비어 있는 데이터 폴더에서 한다. 공고 10개를 넣은 DB에 덮어쓰지 않는다. 복원 도구는 기존 테이블이나 원문을 삭제하지 않는다.
