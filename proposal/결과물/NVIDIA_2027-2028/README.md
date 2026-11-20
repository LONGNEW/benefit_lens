# NVIDIA 2027–2028 실행 결과

공고 1회차, 공고 항목 23개, 경험 3사례와 작성 패턴 2개를 하나의 묶음으로 만들었다. 실제 PostgreSQL 16.15에서 적재와 조회를 확인했다. 기준일은 2026년 11월 9일 한국시간이다.

## 확정한 스키마

[DDL](schema.sql)과 [컬럼 계약](contract.json)을 이 묶음의 v1으로 확정했다. 프로그램, 회차, 항목 종류, 적용 시점, 원문 버전과 사례 연결은 관계형 키로 고정한다. 공고마다 달라지는 자격의 값, 금액의 지급 단위, 문항의 분량과 작성 패턴은 JSONB로 보관한다.

| 구분 | 테이블과 연결 |
| --- | --- |
| 공고 | `program` → `opportunity_round` → `claim`. 자격, 혜택, 수행 의무, 제출물, 양식 상태, 조언을 구분한다. |
| 원문 | `source_document` → `evidence`. 원본과 추출문 해시, 근거의 행 범위와 정확한 앵커를 둔다. `claim_evidence`는 같은 프로그램과 회차만 연결한다. |
| 경험 | `experience_case` → `case_document`. 수상 기록과 지원문의 귀속, 공개 범위와 최종본 여부를 분리한다. |
| 작성 | `writing_pattern` → `pattern_evidence`. 대상 회차의 문항과 과거 사례의 구절을 연결하고, 공식 요구와 사례 관찰을 구분한다. |

수집팀과 작성팀이 같은 공식 근거 ID 네 개에 서로 다른 구절을 붙인 것을 발견했다. 둘 다 같은 원문 버전에 있는지 확인하고, 수집팀의 구절과 공식 안내 역할을 정본으로 합쳤다. [조립 검증](assembly.json)에 기록했다.

## 실제 가져온 공고와 서류

[NVIDIA 공식 공고](https://research.nvidia.com/graduate-fellowships)와 [신청 페이지](https://webportalapp.com/sp/nvidia_grad_fellow_2027)를 다시 확보했다. 접수는 2026년 9월 30일 개시, 추천서 포함 10월 30일 마감, 결과 통지는 2027년 1월 15일 예정이다. 기준일에 날짜상 마감된 회차다. 지원금은 대학이 관리하는 회당 최대 USD 60,000이다. 수혜 학년도 전 대면 여름 인턴십은 이 펠로우십의 활동 의무다.

| 필요한 것 | 확보한 내용 |
| --- | --- |
| 연구계획서 | 공식 작성 안내. 본문과 그림은 최대 2쪽, 참고문헌은 별도, 맨 위에 이름을 적는다. |
| CV | 연락처를 포함해 업로드한다는 안내. |
| 추천서 | 지도교수 포함 2~3통. 포털에서 추천인을 초대하며 마감일까지 도착해야 한다. |
| 인턴십 가능 여부 | 포털에서 가능 여부를 확인한다는 안내. |
| 공식 빈 양식 | 확인한 공개 자료에서는 찾지 못했다. 로그인 전 화면까지만 확인해 내부 양식과 포털 표시 상태는 미확인으로 남겼다. |

첫 박사 연도 완료와 예외 검토를 별도 항목으로 저장했다. 박사 첫해 완료를 교과 수료로 해석하지 않는다. [공고 조회 결과](01_notice.result.json)에 자격, 혜택, 의무와 서류 등 23개 항목이 모두 들어 있다.

## 실제 가져온 경험

| 사례 | 받은 자료와 결과 근거 |
| --- | --- |
| N1 Rohan Yadav | [공개 연구계획서](https://rohany.github.io/reports/nvidia-fellowship-proposal.pdf), [본인 자료 목록](https://rohany.github.io/), [2023년 공식 선정 명단](https://research.nvidia.com/graduate-fellowships/2023). |
| N2 Yuke Wang | [공개 연구문](https://students-at-systems.org/assets/uploads/fellowship-panel/ywang_research_statement.pdf), [패널 후기](https://students-at-systems.org/pages/events/fellowship-panel.html), [2022년 공식 선정 명단](https://research.nvidia.com/graduate-fellowships/2022). 연구문과 패널은 한 사례다. |
| N3 Ruth Fong | [2017 표기 공개 연구문](https://www.ruthfong.com/files/ruth_fong_nvidia_graduate_fellowship_statement_2017.pdf), [미선정 본인 회고](https://medium.com/@ruthfong/applying-to-the-open-philanthropy-ai-fellowship-48dafe7fe27c)의 공개 발췌. 회고 원본 직접 요청은 403이어서 발췌만 남겼다. |

경험은 3사례, 자료는 8개다. 그중 공개 연구문 3개, 색인 1개, 패널 1개, 공식 선정 기록 2개, 본인 회고 발췌 1개다. 공식 선정 사실과 공개 글이 당시 최종 제출본인지 여부는 별개다. 전체 확보물은 공고와 포털 화면을 더해 **원본 9개와 발췌 1개**다. [경험 조회](02_experience.result.json)와 [원문 위치 및 해시](source_manifest.json)를 남겼다.

## 확정한 작성 패턴

1. **연구 질문 → 본인 기여 → 비교 결과 → 다음 검증.** 한 경험이면 병목부터 설명하고, 여러 프로젝트라면 같은 연구 질문으로 묶는다. N1은 문제와 기술 병목, N2는 공통 목표와 여러 프로젝트, N3는 관측 결과와 이후 계획을 연결했다. 공식 문항의 연구 비전과 진행 상황에 맞춰 채택했다.
2. **GPU와 실제로 연결된 부분과 앞으로 확인할 부분을 구분한다.** 이미 한 작업은 사용한 기술, 같은 조건의 결과와 본인 역할로 쓴다. 협업이나 효과가 아직 계획이면 검증할 방법으로 쓴다. N1과 N2에는 수행한 작업이 있고 N3에는 향후 협업 제안이 있다. 이 차이로 선정 원인을 판단하지 않는다.

[패턴과 근거 13개 연결](03_patterns.result.json), [사례별 관찰](case_observations.json), [합성 영어 초안](draft_example.json)을 함께 제공한다. 초안에는 연구 질문, 본인 구현, A100에서의 비교 결과, 다른 조건에서의 다음 검증을 분리해 넣었다. 예시의 수치와 경험은 모두 합성이며 실제 제출본의 분량이나 선정 효과는 평가하지 않았다.

## 실제 검증

[검증 기록](verification.json): 원본과 추출문 해시 19개, 인용 구절 30개를 대조했다. 동일 묶음을 두 번 넣어도 내용과 행 수가 같았다. 다른 회차의 근거와 다른 사례의 문서 연결은 외래 키에서 거절됐고, 패턴을 선정 원인으로 표시하는 입력도 제약으로 거절됐다. 조회의 무결성 위반은 0건이었다.

로컬 Claude의 [검토 회신](claude_review.txt)에서 회고 발췌, 날짜상 마감과 포털 표시, 합성 예시의 구분을 반영했다. 경험 문서가 7개라는 지적은 N1의 색인이 빠진 계산이라 실제 조회의 8개를 유지했다.

이 디렉터리에서 `python3 verify.py`로 같은 묶음을 재현할 수 있다. 보존 원문은 [출처 목록](source_manifest.json)의 로컬 경로에 있으며, 검증은 격리 DB에서 끝냈다.
