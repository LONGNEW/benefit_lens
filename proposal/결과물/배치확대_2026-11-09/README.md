# BenefitLens 여러 회차 배치 확대

2026년 11월 9일 한국시간. NVIDIA에 고정됐던 적재를 여러 회차의 승인 자료를 받는 배치로 바꿨다. 기존 10개에서 **17개**로 확대했다. 11개에는 같은 회차 공식 원문에 연결된 항목이 하나 이상 있고, 6개는 기존 요약 표본이다.

| 추가한 회차 | 확보한 내용 |
| --- | --- |
| [Commonwealth Shared Scholarships](https://cscuk.fcdo.gov.uk/shared/) | 영국 석사 장학 2027–2028. 공식 안내와 준비용 PDF. 제출은 CSC Central. |
| [Chevening 한국](https://www.chevening.org/scholarship/south-korea/) | 한국 대상 2027–2028 장학. 공식 마감 표기, 정확한 마감 시각은 미확인. |
| [NSF GRFP](https://www.nsf.gov/funding/opportunities/grfp-nsf-graduate-research-fellowship-program/nsf26-526/solicitation) | FY2027 공식 공고, 분야별 일정과 연구계획서 형식. |
| [DOE SCGSR](https://science.osti.gov/wdts/scgsr/Key-Dates) | 2026 Solicitation 2 개인 박사 연구체류, 일정과 지원 항목. |
| [Mitacs RISE-Globalink](https://www.mitacs.ca/our-programs/rise-globalink-research-internship/) | Summer 2027 캐나다 대학 학부생의 독일 연구 인턴십. |
| [CU Boulder CHA](https://www.colorado.edu/cha/funding-and-resources/grad-student-opportunities/summer-fellowships) | Summer 2027, 총 USD 6,000, 마감 2027-02-14. [공개 신청 화면](https://www.colorado.edu/cha/content/summer-fellowship-application-form), [예시 PDF](https://www.colorado.edu/cha/sites/default/files/attached-files/cha_summer_fellowship_sample_proposals.pdf) 확보. |
| [Harvard Weatherhead Center](https://www.wcfia.harvard.edu/funding/student/dissertation-writing-grants) | 개인 박사 논문 작성 지원, 한 학기 약 USD 21,650, 마감 2027-02-22. |

마지막 두 회차는 새 표현으로 검색해 찾았다. 원문 검토를 거쳐 적재했고 [검색에서 승인까지의 계보](promoted_from_search.json)를 남겼다. 두 회차의 접수 개시는 미확인이다. 기존 관정 학부 회차도 원문을 다시 받아 학기당 A형 600만 원과 B형 400만 원을 구분했다. [17개 조회 목록](rounds/index.json)에 개별 조건과 출처가 있다.

[사례 7개](experiences/index.json)와 [지침 6개](patterns/index.json)를 적재했다. NVIDIA 외에 Google 동아시아의 영향과 리더십 문항, Gates Cambridge의 특정 박사 과정 연구계획, CU Boulder의 여름 연구계획을 연결했다. Gates 포털의 추가 질문은 보류했다. CU Boulder 지침은 공식 안내에서 만들었고 공개 예시의 선정 여부는 미확인이다.

[PostgreSQL 16.15 검증](verification.json)에서 공고 항목 79개, 자료 41개, 인용 위치 99개를 확인했다. 같은 입력을 두 번 적재해도 내용이 같았고 무결성 위반은 0건이다. 실행 중 승인 자료를 추가했을 때 [15개에서 17개로 갱신](batch_history.json)했다.

검색 17건에서 원문 11개를 확보했고 접근 실패 5개를 기록했다. 검색을 마친 뒤 추출 한도에서 멈춰 텍스트 청크 8개가 대기 중이다. 자동 제안 표현 58개에는 범위 밖 표현도 있어 승인 건수에 포함하지 않았다. [검색 상태](discovery_status.json)와 [검토 대기열](discovery_review_queue.json)을 보존했다.

저장소에서 아래 명령으로 승인 자료를 적재한다. 새 승인 JSON은 `data/batch/approved`에 추가하거나 `--incoming`으로 넘긴다. `--follow-seconds`를 지정하면 그 시간 동안 새 자료를 확인해 다음 배치를 만든다.

```bash
python3 tools/run_benefitlens_batch.py --state .local/batch-run-20261109 --as-of 2026-11-09
```

[검색 실행기](../../../tools/run_benefitlens_discovery_step.py)는 [기존 체크포인트](</home/longnew/Desktop/course_work/tmp/benefitlens_batch_expand_20261109/frontier>)의 누적 한도를 높여 재개할 수 있다. 신규 광역 수집의 완결성과 운영 처리량은 검증 범위 밖이다. 운영 DB는 변경하지 않았다.
