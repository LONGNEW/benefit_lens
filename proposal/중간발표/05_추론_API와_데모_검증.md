# BenefitLens 추론 API와 데모 검증 명세

## 사용자의 질문과 실행 경로

학생이 기본정보를 넣고 공고 하나를 선택한다. 서버는 PostgreSQL에서 공고와 같은 프로그램의 지식을 조회하고, 브라우저는 조건을 판정한다. 이 분리는 개인 정보를 서버에 보내지 않고도 자격 비교를 시연하기 위한 현재 구현이다.

입력은 가상 프로필, 공고 ID와 선택한 공고의 추가 답변이다. 출력은 기본 조건 판정과 경로, 모집 상태, 혜택, 추가 자격, 제출 요구사항, 경험 사례와 작성 개요다. 신청서 제출과 추천인 연락은 수행하지 않는다.

## HTTP 계약

| 요청 | 응답 | 검증 |
|---|---|---|
| `GET /api/pipeline/stats` | 저장 건수와 표본 기준일 | 표본은 학생 개인 예제 10개 |
| `GET /api/pipeline/opportunities?scope=all` | `data[]`, `meta.total`, 페이지 | 현재 allowlist 10개, 기관 연구비 제외 |
| `GET /api/pipeline/detail?id=공고ID` | `data`, `version_hash`, `knowledge` | 공고와 지식의 `program_id` 일치 |
| `GET /assets/pipeline-run.json` | 실행 단계, 해시, 모델 재사용과 시나리오 | `pipeline-run.schema.json` 검사 |

허용되지 않은 공고 ID는 404를 반환한다. DB 오류는 503으로 처리한다. 현재 서버에 개인화 초안을 생성하는 POST API는 없다.

상세 응답의 연결은 다음과 같다.

```json
{
  "data": {
    "opportunity_id": "nvidia-2027-2028",
    "program_id": "nvidia-graduate-fellowship"
  },
  "knowledge": {
    "program_id": "nvidia-graduate-fellowship"
  }
}
```

실제 응답의 `data`에는 공고 전체, `knowledge`에는 사례와 작성 패턴이 포함된다. 경험이 없는 프로그램의 `knowledge`는 null이다.

## 판정 계약

`match.mjs`의 `evaluateEligibility`는 `result: true | false | null`과 `trace[]`를 반환한다. null 트리나 빈 조건은 통과로 만들지 않는다. 같은 함수를 데모와 실행 검증에 사용한다.

`evaluateAdditional`은 답변에 따라 pass, fail, unknown 또는 exception을 반환한다. 활동 요구사항은 기본 자격의 미답변으로 세지 않는다. `applicationState`는 별도로 open, closed, upcoming, rolling 또는 unknown을 계산한다.

표본 기준일은 2026년 10월 15일이다. Google은 기본 박사 조건에 맞더라도 마감 회차다. NVIDIA는 박사 기본 조건에 맞아도 첫 학년과 미래 재학 등 추가 자격이 남는다. 화면은 이를 최종 접수 가능으로 합치지 않는다.

## 시연 순서와 통과 기준

| 순서 | 보여줄 입력 | 기대 출력 |
|---|---|---|
| 1 | 공고 4개와 경험 25개의 저장 원문 | 원본 해시 검사와 텍스트 재추출, 보관 텍스트와 일치 |
| 2 | 저장 공고 10개 | 스키마와 출처 참조 통과, Gates는 미추출 |
| 3 | 경험 원문 전체와 기존 모델 실행 기록 | 3개 입력 해시 일치, 14사례와 17패턴 근거 재검증 |
| 4 | 같은 표본을 DB에 두 번 적재 | 두 번째 적재에서 버전, 변경 이력과 갱신 시각 유지 |
| 5 | 박사 프로필, 학부 GPA 경계값과 추가 답변 | 11개 시나리오 기대값 일치 |
| 6 | NVIDIA `research-arc` 패턴 | 본인의 문제, 판단, 결과와 계획을 채울 빈칸 개요 |

GPA 미달, 공식 만점 분기 없음, 미추출 조건, 교과 수료 전후, NVIDIA 예외와 마감 회차를 함께 보여준다. 이는 코드 동작의 검사이며 의미 추출 정확도를 측정한 실험은 아니다.

## 실행 기록과 실패 처리

`make pipeline-replay`는 성공 시 `.local/pipeline-replay/latest.json`과 공개 `web/pipeline-demo/pipeline-run.json`을 저장한다. 원문 전문, 로컬 파일 경로와 실사용자 이력은 공개 기록에서 제외한다. 모델의 원래 실행 시각과 이번 재검증 시각을 나눠 저장한다.

스키마 오류, 원문 변경, 잘못된 인용, 모델 입력 해시 불일치, 다른 DB, 중복 적재 변화 또는 HTTP 시나리오 실패는 실행을 실패시킨다. DB 적재는 한 트랜잭션으로 수행한다. 적재 후 HTTP 검사가 실패하면 DB의 검증된 표본은 남고 성공 실행 기록은 교체하지 않는다.

기본 표본 데모는 원문 없이 `make sample-db`, `make demo`로 실행할 수 있다. 원문부터 재검증하는 경로에는 별도로 보관한 원문과 모델 실행 캐시가 필요하다.
