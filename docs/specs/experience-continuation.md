# 후기 수집의 지속 실행과 원천 확장

2026-11-19 갱신. `tools/continue_benefitlens_experiences.py`가 기존 큐와 원문 저장소를 이어 사용한다. 수집 과정은 Python으로 실행하며 에이전트나 LLM을 호출하지 않는다.

## 실행

```bash
/usr/bin/python3 tools/continue_benefitlens_experiences.py \
  --continuous --target 50000 --max-requests 100000 --workers 8 --seed-batch 100
```

로컬 서비스 `benefitlens-experience-scale.service`의 `continuous.conf`에서 이 명령을 실행한다. 실행별 네트워크 요청 예산은 100,000회다. 제공자가 정한 요청 한도와 접근 제한은 별도로 적용한다. 사용자 서비스는 현재 로그인 세션에서 관리되며 재부팅 후 자동 시작은 설정하지 않았다.

```bash
systemctl --user status benefitlens-experience-scale.service --no-pager
journalctl --user -u benefitlens-experience-scale.service -n 10 --no-pager
systemctl --user stop benefitlens-experience-scale.service
systemctl --user start benefitlens-experience-scale.service
```

초기 목표는 경험 50,000건이다. 기존 커버리지 정책에 따라 전체 공고 후보 36,856건의 3배인 110,568건을 계획 목표로 함께 추적한다. 공고 후보 수는 현재 접수 중인 검증 공고 수와 구별한다.

## 큐가 비었을 때

1. 설정에 등록한 추가 목록, GSoC 공유 디렉터리에서 관찰된 외부 링크, 이미 받은 WordPress 전문과 보고서를 영속 커서 다음 위치부터 추가한다.
2. 실제 목록의 하위 학과, 국제처, 국가별 보고서와 공개 RSS 링크를 따라간다. 새 호스트는 출처 정의와 발견 경로를 저장한다.
3. 요청 간격이나 서버 한도 때문에 대기 중인 작업이 있으면 재개 시각까지 기다린다. 긴 `Crawl-delay`는 작업 큐의 재시도 시각으로 넘겨 다른 호스트를 처리한다.
4. 등록한 발견 스트림까지 소진되면 `waiting_for_source_discovery`로 대기한다. 다음 날짜에는 성공했던 목록을 다시 확인하고 설정 파일이 바뀌면 새 원천을 읽는다.

새 작업을 만들 단서가 없을 때 웹 전체를 임의로 탐색하지 않는다. 대학 공식 홈페이지의 관찰된 링크, 프로그램 명단의 블로그 링크와 등록한 디렉터리가 확장 경로다. 403, robots 금지와 명시적 수집 금지는 보류한다. 접근 거부된 목록은 매일 자동 재시도하지 않는다.

## 자료의 역할과 집계

| 상태 | 저장 내용 | 경험 수 포함 |
| --- | --- | --- |
| pending, retry | 발견 URL, 발견 경로, 다음 처리 시각 | 제외 |
| indexed | 목록 원본과 관찰된 하위 링크 | 제외 |
| reference_material | 원문, 추출 전문, 프로그램 후보, 작성 참고 역할 | 제외 |
| needs_review | 파싱 또는 연결 확인이 필요한 기록 | 제외 |
| accepted | 명시된 프로그램과 당사자 행동의 원문 구간을 보존한 자동 수집 사례 | 중복 확인 후 잠정 집계 |
| policy_held | 접근 제한과 보류 이유 | 제외 |

새 사례의 `semantic_review`는 `unreviewed`다. 선정 결과와 유급 여부를 서술에서 입증하지 못하면 미확인으로 둔다. 작성 패턴 승인은 별도 검토 작업이다. 일반 작성 지침이나 여러 프로그램이 섞인 글은 참고 자료로 남긴다.

공개 작성자를 확인하면 같은 프로그램과 회차의 여러 글을 한 사례에 연결한다. 회차 미상 글은 게시 연도로 회차를 만들지 않는다. 같은 URL과 정규화 본문은 기존 DB와 대조한다. 익명 글을 서로 다른 사람으로 확정하지 않는다.

## 저장과 재시작

공유 캐시 `scale50k-20261109/experience-scale-v1` 아래에 `exploration.sqlite`, 압축 원문과 전문, `continuous-status.json`, `continuous-runtime.json`을 저장한다. 실제 경로는 `/mnt/storage/longnew-shared-cache/datasets/benefitlens/scale50k-20261109/experience-scale-v1`이다.

단일 실행 잠금으로 중복 수집기를 막는다. 종료 신호를 받으면 진행 중 작업을 반영하고 체크포인트를 남긴다. 재시작하면 중단된 작업을 회수하고 커서 다음 위치부터 이어간다. 같은 입력으로 다시 실행해도 큐와 문서, 사례가 중복 증가하지 않도록 키와 본문 해시를 사용한다.

체크포인트마다 원문과 사례를 PostgreSQL로 인계한다. 인계 오류는 `postgres_error`에 기록한다. `accepted_cases`는 수집기 내부 사례 수이고, `experience_coverage.counted_experience_episodes`는 기존 자료까지 합친 DB 중복 집계이므로 서로 다르다. 원천별 URL 처리 건수를 사람 수나 사례 수로 합산하지 않는다.

## 대량 자료와 기관 탐색

다음 Python 서비스가 서로 다른 큐를 소유한다. 부팅 시 자동 시작은 설정하지 않았다.

| 서비스 | 역할 |
| --- | --- |
| benefitlens-university-registry-continuous-20261119 | 공개 기관 후보에서 관찰된 국제처와 후기 보관함 링크 탐색 |
| benefitlens-university-archives-20261119 | 발견 manifest를 읽어 공개 보고서 본문 수집 |
| benefitlens-participant-documents | 공개 저장소와 참가자 기록의 원문 수집, 제공자 한도에 따른 대기 |
| benefitlens-experience-scale | 기존 경험 큐 처리, 새 staged 자료 검증과 PostgreSQL 인계 |

기관 참조목록은 Hipo의 commit 고정본이다. 기관 후보 10,272개 중 선택한 10개국의 4,374개를 국가별 순환 배치로 탐색한다. 이 목록의 웹 주소를 기관 공식성이나 후기 확보의 증거로 사용하지 않는다. 발견한 보관함 manifest를 본문 수집기가 반복해서 읽는다. 로그인으로 이동한 PDF와 권한 거부 응답은 원문 확보에서 제외한다.

`experience_expansion_sources_v1.json`의 `staged_collections`에 캐시 내부 index 경로를 등록한다. 대학 보고서, 참가자 자료와 커뮤니티 작성 자료가 같은 문서 인계 계약을 사용한다. 각 레코드는 `state=body_staged`, `url`, `original`, `text`를 갖고, 원문과 추출문 참조의 SHA256을 검증한다. 저자, 세부 문서 역할, 라이선스와 발견 경로도 전달한다. 참고 자료는 `material_only=true`로 등록한다.

PDF, HWP v5, HWPX와 DOCX의 본문을 지원한다. XML 문단 순서를 보존하고 일반 ZIP 묶음을 한 사람의 경험으로 합치지 않는다. 저장소 묶음은 commit과 Git blob을 검증한 개별 문서로 분리한다. 원문 시각은 보존한 ZIP 또는 API 수집 영수증에서 얻는다. 참고 자료의 메타데이터 정정은 같은 문서 ID를 유지해 다시 인계한다.

커뮤니티는 2026-09-30 공개 재배포본의 전체 질문과 답변 360,256건을 `community-bulk-20261119/corpus.sqlite3`에 보존했다. 공식 최신 덤프의 로그인 경로는 사용하지 않았다. 작성 관련 기계 선별 후보 37,944건 중 기존 두 라이브러리와 겹치지 않는 24,383건과 기존 부모질문 1건을 공통 문서 인계 대상으로 삼는다. 질문 연결, XML 원래 속성, 게시물 HTML, 추출문과 라이선스를 유지한다. 선정 답변 여부는 지원자의 합격 결과로 해석하지 않는다.

재현 명령은 다음과 같다. 네트워크 수집과 기존 원문의 로컬 인계를 구분한다.

```bash
/usr/bin/python3 tools/collect_benefitlens_community_bulk.py
/usr/bin/python3 tools/stage_benefitlens_community_documents.py
```

`--import-batch`의 기본값은 500이다. 대량 문서 인계 중에도 사례 수와 승인된 작성 패턴 수를 따로 집계한다. 문서명이나 태그가 직접 연결된 후보, 본문에 문맥만 있는 후보, 일반 글쓰기 자료는 후속 의미 검토의 범위를 구분하는 정보다.

## 확장 전망과 현재 제약

대학별 보고서 목록, 프로그램별 참여자 블로그와 기존 API 전문을 함께 처리하면 단일 사이트 중심의 범위를 넓힐 수 있다. 기관 메타데이터를 공식 홈페이지의 출발점으로 쓰는 ROR 경로도 구현했으나 이번 확인에서는 API가 403으로 보류됐다. 공식 대량 배포의 Zenodo API 경로도 robots 규칙으로 보류됐다.

독일 대학 자료는 교환학생 경험에 치우친다. 지원서 작성에 쓰려면 지원 동기, 준비 과정, 선택 이유와 실제 문항에 대한 답을 찾아 역할을 구분해야 한다. 한국 관련 지원 준비 후기와 실제 제출문 확보량은 계속 별도 확인해야 한다. 현재 등록 원천만으로 50,000건 달성을 보장할 근거는 부족하다.

## 2026-11-18 추가 확장

버클리의 지원서 및 심사 의견, MIT의 주석 달린 예시, 상명대의 교환학생 보고서 목록을 등록했다. 예시 모음은 `material_only`로 지정해 당사자 경험으로 자동 집계하지 않는다. 상명대 자료는 원문에 프로그램 표현과 당사자 행동이 함께 있을 때 검토 대기 사례가 된다. 프로그램 표현은 원천별 `program_bindings`에 등록하며 결과와 지원 회차는 별도 근거가 없으면 미확인이다.

한국 대학의 쿼리형 첨부 URL은 링크에 표시된 PDF 파일명으로 발견한다. 상명대의 해당 다운로드 경로에는 32 MiB의 로컬 응답 상한을 적용한다. ZIP과 HWP 첨부는 이 파서로 읽지 않는다. 대용량 묶음 문서 하나를 학생 수로 분할 집계하지 않는다.

공개 제안서 저장소는 아래 선택 다운로드 도구로 수집한다. 커밋과 Git blob, 원문 및 텍스트 해시를 검증하고 기존 수집기의 `body_staged` 계약으로 전달한다.

```bash
/usr/bin/python3 tools/collect_benefitlens_repository_materials.py
```

기본 원천은 devweekends/open-source-proposals이며 파일별 32 MiB, 한 실행 최대 300개로 제한한다. `--retry-size-holds`는 로컬 크기 제한으로 보류했던 파일만 재시도한다. 접근 거부를 재시도하는 옵션은 아니다.

원천별 index.json이 갱신되면 실행 중인 수집기가 새 작성 자료를 읽는다. 자료 묶음과 입력 해시별 커서를 사용해 목록 앞에 추가된 자료도 발견하고, 기존 URL은 큐에서 중복 제거한다. 작성 자료와 경험 사례, 승인된 패턴은 각각 집계한다.

문항별 작성 조언은 `tools/collect_benefitlens_question_knowledge.py`의 공개 API 검색 및 답변 페이지를 이어 수집한다. 검색 표현 연결은 미검토 상태이며 실제 신청 문항이나 확정된 평가 기준으로 승격하지 않는다. API의 backoff와 quota는 체크포인트에 보존한다.
