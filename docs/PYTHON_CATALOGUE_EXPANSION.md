# 공개 모집 목록을 이용한 원천 확장

2026-11-15에 추가했다. 수집과 일일 갱신은 Python으로 실행한다.

## 표현 검색 외에 추가한 경로

1. K-Startup 공식 모집 목록의 대학생 필터를 적용하고 게시된 페이지 번호를 순회한다. 상세 공고의 신청대상, 제외대상, 접수기간, 지원내용, 신청 링크와 이메일을 추출한다. 모든 페이지에 반복되는 강조 공고는 목록 결과에서 제외한다.
2. 공식 대학 명부의 홈페이지에서 공지와 학생지원 메뉴를 따라간다. 학교별 최대 6개 탐색 주소와 4개 게시판을 대상으로 하며, 게시판의 모집 원문을 별도로 읽는다. 로그인 경로와 다른 기관으로 향하는 리다이렉트는 따라가지 않는다.
3. 이미 관찰한 Greenhouse 개별 공고 URL에서 소속 게시판을 식별한다. 공개 Job Board API로 해당 게시판의 전체 게시물을 받고 학생 직무 후보를 분리한다. API는 공개 GET만 사용한다.

K-Startup의 상세 화면은 “예비 창업자와 창업기업을 위한 창업지원사업공고를 확인하실 수 있습니다.”라고 설명한다. 대학생 필터에 표시되어도 기업 설립, 지역, 사업 선정 이력 등의 조건은 상세 공고에서 다시 읽어야 한다. [공식 모집 목록](https://www.k-startup.go.kr/web/contents/bizpbanc-ongoing.do)

Greenhouse 문서에는 “authentication is not required for any GET endpoints.”라고 명시되어 있다. 본문을 포함하는 `content=true` GET을 이용한다. [공식 API 문서](https://docs.greenhouse.io/job-board.html)

## 단계와 건수

`목록 발견 → 원문 읽기 → 접수와 자격 검토 → 지급 조건 대조 → 중복 통합 → 인계`

목록 항목과 읽을 수 있는 문서는 확보한 지원 기회 수로 합산하지 않는다. 같은 기관과 프로그램의 대학별 재게시, 동일 기업의 같은 직무 변형은 별도 검토한다. 학교 소속, 지역 연고, 국적과 취업 허가 조건을 기록해 사용자의 실제 지원 가능성을 판단한다.

K-Startup의 `awaiting_review`는 개별 검토 대상이다. 무료 시설과 바우처, 사업비, 개인에게 지급하는 돈을 검토에서 구분한다. API에 현재 게시된 학생 직무 역시 지급 근거와 한국 학생의 지원 조건을 확인해야 한다.

## 수정한 추출 문제

- Greenhouse는 `absolute_url`, `content`를 사용하지만 기존 JSON 파서는 주로 Ashby의 `jobUrl`, `applyUrl`을 처리했다. Greenhouse 본문과 식별자를 읽도록 수정했다.
- `hwp5txt`는 표를 `<표>`로 대체했다. 표 안의 제외 조건을 보존하기 위해 `hwp5proc xml`의 문서 본문과 표 셀 텍스트를 추출한다.
- 대학 게시판의 게시판 번호와 글 번호를 구분한다. 같은 학교의 공개 HTTPS 리다이렉트만 제한적으로 따라간다.
- 기관별 본문 선택자가 맞지 않거나 이미지에만 정보가 있는 문서는 보류한다.

## 실행과 저장

프로젝트 루트에서 실행한다.

```bash
python3 tools/collect_benefitlens_kstartup.py
python3 tools/discover_benefitlens_university_boards_v2.py
python3 tools/discover_benefitlens_university_boards_v2.py --refresh-discovered
python3 tools/expand_benefitlens_public_boards.py
```

추가된 일일 서비스는 KST 06:40 K-Startup, 06:55 기업 공개 목록, 07:10 대학 탐색 순서다. 대학 탐색 결과는 기존 국내 일일 수집기가 읽는 원천 목록에도 연결된다.

캐시 루트는 `/home/longnew/.cache/datasets/benefitlens/scale50k-20261109`다. 실제 저장소는 승인된 `/mnt/storage/longnew-shared-cache/datasets/benefitlens/scale50k-20261109`이며 원문과 추출문은 해시를 붙여 압축 보관한다.

| 하위 경로 | 내용 |
| --- | --- |
| `catalogue-expansion-v1` | K-Startup 목록, 본문, 검토 후보 |
| `korea-campaign-v1/university-discovery-v2` | 대학별 탐색 경로, 게시판, 원문 상태 |
| `public-board-expansion-v1` | 기업 게시판의 관찰 근거, 공개 JSON, 학생 직무 후보 |
| `catalogue-expansion-review-v1` | 첨부 공고문과 개별 검토 인계 자료 |

각 경로의 `status.json`은 수집 단계의 상태다. 개별 검토 인계 자료와 전날의 전체 검증본은 날짜별로 구분한다. 운영 DB 반영과 5만 건 달성 여부는 별도 집계한다.
