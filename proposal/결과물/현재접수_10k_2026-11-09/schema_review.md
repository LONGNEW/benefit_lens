# 최종 등록기 독립 검토

검토 대상은 tools/build_benefitlens_live_batch.py, run_benefitlens_batch.py와 DB 원문 검증 코드다. 원문 추가 다운로드나 코드 수정은 하지 않았다. 아래 결과는 저장된 실제 자료의 반례 재현과 변환 검산에 근거한다.

## 코드 필수 수정 1건, 반영 확인

[P1, 해결] Walmart 추가 상태 문서가 선택 사항이어서 보류한 공고가 재유입될 수 있었다. 실제 R-2569652의 공식 SSR에는 `"positionAvailable":0`, `"source":"WD"`, `"requisitionStatus":{"id":"OPEN"`가 함께 있다. 기존 Workday 1차 입력은 `canApply=true`, `posted=true`만 가지고 있었고, 수정 전 verify_posting과 tables_for 이후 validate_sources를 모두 통과했다. 따라서 최종 승인 자료 대신 옛 1차 입력을 섞으면 보류 8건이 open으로 들어갈 수 있었다.

root가 workday/walmart의 frontend_status_source를 필수로 바꿨다(등록기 221행). 같은 실제 입력은 이제 거절된다. 0자리 공고를 현재 가용 자리로 세지 않겠다는 이번 집계 규칙이 코드에도 적용됐다. 보류 8건이 이미 최종 승인본에 포함됐다는 발견은 아니다. 수정 후 회귀18개가 통과했다. root의 첫 SQL 실행은 doctoral_studentship17개가 허용 enum에 없어 거절됐다. admission_funding으로 명시 변환하고 미등록 유형은 거절하도록 수정했다. 원천 유형과 제목 기반 추론의 근거를 보존하며, 수정 후 회귀19개를 독립 실행해 통과했다.

## 나머지 핵심 경로의 확인 결과

1. 식별자와 중복: Workday는 공개 URL의 실제 tenant와 native requisition ID로 합친다. 사이트 별칭, 여러 지역과 재수집 건을 별도 공고로 늘리지 않는다. 동일 시각의 원문 해시 또는 핵심 해석값 충돌은 거절한다. 다른 시각은 각각 먼저 검증한 뒤 최신 관측을 택하고 감사 기록을 남긴다. 이 경로에서 추가 집계 오류를 재현하지 못했다.

2. 공식 상태: Workday의 jobReqId, externalUrl, canApply와 posted를 원본 JSON에서 대조한다. Walmart의 active=false는 이번 WD 공고의 접수 거절 조건으로 쓰지 않는다. 공식 SSR의 동일 ID, source=WD, OPEN과 양수 자리 수를 결합한다. DAAD는 실제 active 버튼과 공식 JS의 URL 생성 규칙을 검사한다. 일반 HTML의 신청 링크는 기간과 해당 원천에서 검토한 상태 근거를 함께 사용한다.

3. 다중 문서: 보조 문서마다 당일 수집, 같은 기관, 적용 범위와 원본 및 텍스트 해시를 확인한다. 실제 Cambridge 50건을 변환한 결과 문서 103개, guideline 근거 184개이며 원문 해시와 인용 위치 검사가 통과했다. 과정과 장학금의 적용 관계는 support_scope에 보존된다. 기관이 같다는 사실만으로 과정 적용 관계를 자동 판정하는 기능은 구현되어 있지 않다.

4. 날짜: 실제 Mercedes 321건 중 게시 종료 320건은 SQL closes_on에 들어가지 않고 JSONB publication_ends_on으로 보존됐다. 나머지 MER00049EO 장학금은 원문의 `12 November 2026` 신청 마감이므로 closes_on에 남는다. Schneider의 정확한 게시 종료 시각도 신청 마감과 분리한다. 날짜가 없는 공고는 null이며 종료일을 만들어 넣지 않는다.

5. SQL과 JSONB: 공고 식별자, 회차, URL, 확인일과 신청 기간은 관계형 열에 둔다. 학생 대상 여부, 상태 판정 방식, 게시 기간, 기관 제한과 FWS, 유급 근로는 근거가 연결된 claim JSONB에 둔다. 실제 변환에서 `student_scope_verified=true`, `full_eligibility_verified=false`가 보존된다. 현재 적재 범위는 학생 대상 공고와 접수 경로의 확인이며 모든 개인 조건의 구조화는 후속 작업이다.

## 고정 입력 추가 검토, 10건 보류 반영

최종12입력의 제목과 본문 날짜를 추가 검토했다. 237개 날짜 구절과220개 문서에서 고정 과거 기간 또는 날짜 충돌10건을 찾았다. AMAT R2616095, J&J R-104258, PwC731241WD, Siemens524091, Amazon3127861/10378826/10564600/10564598, Bosch744000155097535, UNSW1989-7245다. API가 공개 접수를 표시해도 실제 인턴 기간이2026년여름까지이거나, 새 제목과 과거 본문 또는 상충하는 두 마감이 함께 있었다.

root가 정확 구절과 원문SHA를 대조한 뒤10건을 최종 입력에서 제외했다. 판정은 마감 확정 대신 ambiguousheld이며, 원문과 철회 근거를 보존한다. 고정 입력은10733관측/10486고유공고에서10723관측/10476고유공고로 바뀌었다. Philips591552의 제목2025는 현재 게시일2026-09-18 및 재학 조건과 함께 검토했다. 본문에 고정 과거 신청 기간이 없고 졸업 예정일은2026년1월 이후이므로 유지했다.

## 최종 선정본 독립 집계

추가 팀 검토까지 반영한 최종12입력은10718관측/10471고유공고/중복관측247개다. 선정본10000행은10000개의 서로 다른 provider/native ID이며, 고정 입력의 정확한 부분집합이고 각ID의 최신 main 관측을 선택했다. 동일URL에 다른ID를 붙인 입력은0개다. 알려진 보류15건, 명시 마감 경과나 미래 개시, 비당일 관측, 경험/작성 패턴 스키마 행은 선정본에0개다. Walmart655건은 모두 추가 공식 상태 문서를 보유한다.

상위 원천의 기관은 CVS Health1838, Walgreens870, Thales687, Walmart655개 공고다. 같은 기업의 점포별 공고도 고유 requisition이면 포함되므로10000개 프로그램군으로 설명하지 않는다. 신청 마감 확인2936건, 마감 미공개/미파싱7064건이며 게시 종료320건은 신청 마감에서 분리했다. 검산 파일은 final_selection_audit.json이다.

## 실제 SQL 결과 교차 대조, 완료

PostgreSQL16.15의 실제 조회10000행을 선정본과 직접 대조했다. provider/native 집합과 program_id 해시, 회차, 공식 URL, 신청 URL, 신청 마감, 상태와 확인일의 불일치는0이다. 원천94개, source_backed/open 각10000건이며 사례와 작성 패턴은 각0건이다. SQL 유형은 internship9088, scholarship241, other654, admission_funding17이다. 원본 및 텍스트 해시 각10710개와 인용24089개가 검증됐고, 스키마 위반0건, 같은 입력 재적재 불변이 확인됐다.

집계는 고유 공고 게시물 단위다. 모든 개인 자격을 확인한 full_eligibility_verified는10000건 모두false다. 신청 제출은 실행하지 않았다. 선정본 검산은 final_selection_audit.json, 실제 SQL 대조는 final_sql_result_audit.json에 보존했다. 코드 반례의 재현은 final_importer_review_checks.json에 남겼다.
