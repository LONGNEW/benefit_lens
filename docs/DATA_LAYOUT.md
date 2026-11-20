# 학생 개인 대상 예제 10개의 저장 구조

현재 활성 범위는 `pipeline/config/student_demo_scope.json`의 공고 10개다. 일반 기관 연구비와 교원 전용 사업을 제외하고 신규 수집을 중지한다.

## 핵심 테이블

| 테이블 | 행의 단위 | 저장하는 내용 |
|---|---|---|
| `opportunities` | 공고 또는 제도 예제 | 프로그램 ID, 회차, 조건 트리, 혜택과 공식 문항 |
| `program_knowledge` | 프로그램 | 사례 배열, LLM 작성 패턴과 입력 근거 |
| `pipeline_document` | 원문 버전 | URL, 해시와 원문 및 텍스트 파일 경로 |
| `pipeline_work_item` | 처리 작업 | 상태와 사유 |
| `opportunity_revision` | 공고 버전 | 변경 이력 |

이들은 모두 PostgreSQL의 관계형 테이블이다. `eligibility`, `experience_cases`, `writing_pattern`처럼 중첩된 데이터는 JSONB 컬럼으로 저장한다. 현재 사례와 패턴 각각을 별도 테이블에 한 행씩 저장하는 형태는 아니다.

공고와 지식은 `program_id`로 LEFT JOIN한다. 한 프로그램의 여러 회차가 같은 경험을 재사용할 수 있다. 원문 전체는 로컬 캐시에서 모델 입력으로 읽으며 공개 Git에는 원문을 넣지 않는다.

## 모델 전처리

`writing_patterns.py`가 기존 경험 원문 전체를 LLM에 보내 문서별 분석과 사례별 조각, 평가 기준별 근거와 선정 및 미선정 비교 및 프로그램별 패턴을 만든다. 원문과 프롬프트 해시, 모델, 문서 수와 인용을 검사하고 기록한다. `make patterns`로 실행한 뒤 `make sample-db`로 적재한다.

원천 14만 건과 기존 단수형 `opportunity` 등의 테이블은 과거 수집 및 구현 자료다. 활성 API의 `scope=all`도 현재 선택한 학생 예제 10개를 벗어나지 않는다. 실행 중인 Python 데모와 과거 Next/Vinext 및 D1 경로를 구분한다.

평가 기준별 `criterion_observations`와 결과 집단별 `outcome_comparisons`도 `program_knowledge.payload` 안에 저장한다. 인용은 입력 문서와 사례 ID에 연결한다. 선정 상태는 심사 원인과 분리하며 별도 테이블을 추가하지 않았다.
