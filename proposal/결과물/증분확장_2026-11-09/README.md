# 출처와 공통 표현, 작성 자료의 증분 확장

2026-11-09. 기업 인턴 중심의 기존 원형에 장학금, 학비 감면과 생활 지원의 공식 출처를 추가했다. 기존 공통 매핑과 NVIDIA 사례를 유지하면서 새 수집 자료와 작성 자료를 연결했다.

## 현재 접수 자료는 얼마나 늘었는가

| 집계 단위 | 기존 원형 | 확장 후 | 증가 |
| --- | ---: | ---: | ---: |
| 기간과 학생 범위를 확인한 원천 모집 변형 | 4,339 | 4,378 | 39 |
| 기관과 직무 규칙의 부모 후보 | 1,129 | 1,165 | 36 |
| 그룹화 규칙을 통과한 부모 후보 | 1,071 | 1,107 | 36 |
| 추가 검토 대상 부모 | 58 | 58 | 0 |

기존 목록과 새 목록을 합쳐 같은 그룹화 규칙을 실행한 결과다. 같은 기관의 독립 장학 제도와 같은 제도의 신입생용 및 재학생용 모집 변형을 구분했다. 의미적으로 전수 검수한 고유 기회 수는 아직 확정하지 않았다. 10,000개 확보 목표에도 도달하지 않았다.

새 출처는 Auckland 장학금 25개, Emmanuel 학생 지원 9개, Swansea 학비 감면 1개, 중국정부 장학금의 Sydney 신청 경로 1개, NZSEE와 NMIT, HPSNZ 각 1개 모집 변형이다. Emmanuel의 9개 변형은 6개 이름 있는 장학 제도로 묶였다. 기존에 있던 기숙사 장학 관련 중복 후보 4개는 추가 수에서 제외했다. 이번 추가분은 뉴질랜드와 호주 대학 장학금에 편중되어 있다.

공식 원문을 실제 확보하고 날짜와 학생 대상 근거를 파싱한 자료만 위 표에 넣었다. GOV.UK 석사 및 박사 대출 2개는 공식 원문을 확보했으나 별도 후보로 보존했다. 과정의 최종 학년도 시작일을 기준으로 계산하는 상대 마감을 현재 기간 계약이 지원하지 않아 정식 확보 수에 넣지 않았다.

## 공통 표현은 무엇을 더 채웠는가

이전 공통 매핑 7개를 유지하고 27개를 추가했다. 기존 원형에서 19개, 새 수집 자료에서 8개다. 기존 사실과 같은 매핑 3개는 중복으로 제외했다.

- 유형의 의미 검토 16개: 장학 10개, 인턴 5개, 학생 근로 1개.
- 지원금과 지원 방식의 구조화 10개.
- 같은 지원금을 여러 용도로 사용할 수 있는 공유 용도 집합 1개.

기존 원형의 SQL program.opportunity_type=other를 일괄 변경하지 않았다. 검토한 분류는 근거가 있는 advice/opportunity_classification claim과 분류 overlay로 조회한다. 기존 원형에는 분류 후보 4,274개와 미확인 53개가 남아 있다. 새 수집 자료에서는 4개 모집 변형의 유형과 지원 내용을 추가 검토했으며 나머지 35개도 세부 의미 검토가 필요하다.

새 자료에서 직접 연결한 예시는 다음과 같다. 표의 기호는 원문 표기를 보존한 것이며 환산용 통화 코드는 미확인으로 남겼다.

| 공식 원문 | 저장한 지원 내용 |
| --- | --- |
| [Swansea International Excellence](https://www.swansea.ac.uk/international-students/my-finances/international-scholarships/international-excellence-scholarships/) | 첫 학년도 학비 감면, 최대 £5,000 |
| [NMIT Fifeshire 및 Hancock-Pollard](https://www.nmit.ac.nz/study/finance-your-study/scholarships) | 생활비 $10,000, 지급 방식 미확인 |
| [Emmanuel Dr Diana Cavaye](https://www.emmanuel.uq.edu.au/scholarship/dr-diana-cavaye-scholarship-new/) | $5,000 지원, 사용 목적 미확인 |
| [Auckland Murray Wren](https://www.auckland.ac.nz/en/study/scholarships-and-awards/find-a-scholarship/murray-wren-masters-scholarship-in-architecture-815-eng.html) | 연간 최대 $15,000, 최대 2년이라는 조건 보존 |

지원액 한도를 개인의 확정 수령액으로 바꾸지 않았다. 학비와 생활비에 함께 사용할 수 있는 하나의 지원금도 금액을 두 번 더하지 않는다. 개인의 전체 자격 판정은 별도 과제다.

## 경험과 writing pattern은 어떻게 확장했는가

NSF GRFP와 Fulbright를 추가했다. 공식 문항 2개 출처, 공개 지원문 2개, 본인 후기 및 문서 색인 1개, 대학 인터뷰 1개로 원문은 6개다. 인물은 2명이고 경험 사례도 2개다. 한 사람이 쓴 연구문과 개인문을 서로 다른 사람으로 세지 않았다.

공식 제출 요구 claim 3개와 제한된 작성 지침 2개를 검토해 연결했다. 이 통합본에서는 기존 NVIDIA 사례 3개와 지침 2개도 유지하여 사례 5개, 지침 4개를 연결했다. 이는 이번 검증 통합본의 범위이며 기존 공개 표본 전체의 사례 총수와 다르다.

별도로 NSF 경험 문서 3개 전체 20,169자를 whole-experience-evidence-v3 입력으로 사용했다. 실제 LLM 호출 1회가 완료되어 패턴 후보 5개를 반환했다. 입력을 잘라내지 않았고 결과를 별도 receipt로 보존했다. 새 후보 5개를 승인 지침 수에 더하지 않았다. 비교할 미선정 사례가 없어 no_comparable_pair를 유지했다.

공개문이 최종 제출본인지 확인되지 않은 범위, 인터뷰와 제출문의 차이, 공식 문항의 현재 규격을 보존했다. 사용자 사실이 없는 통합 출력은 질문 13개와 초안 슬롯 3개이며 완성 문장은 생성하지 않았다.

## 실제 검증과 남은 범위

전체 결합에서 문서 스냅샷 8,323개와 인용 25,709개의 해시 및 위치를 검증했다. 승인된 claim 13,175개와 패턴 4개를 SQL 적재 형식으로 투영했고, 보류 claim 67개는 감사 기록에 남겼다.

변경 자료와 이전 검토 프로그램을 고른 58회차의 표본은 격리된 PostgreSQL에 실제 적재했다. claim 206개, 사례 5개와 패턴 4개를 포함하며 계약 위반은 0건이었다. 동일 입력 재적재 결과도 같았다. 전체 4,378개 현재 모집 변형을 새 운영 DB에 전량 적재한 결과는 아니다.

관련 회귀 검사 181개가 통과했다. 전체 인계와 SQL 검증 표본을 다시 실행해 저장 결과 재사용과 결과 해시 불변을 확인했다. 큰 원형 파일은 추가 복사하지 않았으며 운영 DB와 기존 공개 표본은 변경하지 않았다.

재현은 [증분 확장 명세](../../../docs/specs/expansion.md)의 CLI를 따른다. [검증 결과](validation.json)에는 고정 manifest, 전체 검증 receipt와 SQL 표본 경로를 담았다. 원문과 재사용 데이터는 공용 캐시를 참조한다.

팀별 상세 기록은 [수집팀](/home/longnew/Desktop/course_work/tmp/benefitlens_expansion_20261109/discovery/team_report.md), [기존 자료 스키마 검토](/home/longnew/Desktop/course_work/tmp/benefitlens_expansion_20261109/schema/team_report.md), [새 자료 스키마 검토](/home/longnew/Desktop/course_work/tmp/benefitlens_expansion_20261109/schema/new_sources_report.md), [작성팀](/home/longnew/Desktop/course_work/tmp/benefitlens_expansion_20261109/writing/team_report.md)에 있다.

일일 자동 갱신, 상대 마감의 개인별 계산, 전체 자격 조건과 지원 유형의 전수 정규화, 제품 API 연결과 완성 초안 생성은 남아 있다. 검색 포괄성, 의미 정확도와 작성 부담 감소도 아직 측정하지 않았다.
