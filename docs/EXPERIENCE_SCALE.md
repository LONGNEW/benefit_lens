# 경험 수집기 실행과 집계

2026-11-18 현재 서비스는 [지속 실행과 원천 확장](specs/experience-continuation.md)의 `continue_benefitlens_experiences.py`를 실행한다. 아래는 기존 수집기와 공통 저장 및 집계 규칙이다.

`tools/scale_benefitlens_experiences.py`는 Python으로 공개 목록과 원문을 수집한다. 에이전트와 LLM을 호출하지 않는다. 초기 목표는 경험 사례 50,000건이며 현재 확보량과 다르다.

```bash
/usr/bin/python3 tools/scale_benefitlens_experiences.py --target 50000 --max-requests 100000 --workers 6 --reparse
```

요청 예산은 실행마다 새로 계산한다. 사이트의 요청 한도, robots, 인증 제한과 기존 호스트 보류는 유지한다. GFG는 자동 수집을 금지한 약관에 따라 비활성화했다. 링커리어는 관찰된 403 때문에 비활성화했다.

공개 목록과 후보 작업은 기존 Frontier 형식의 SQLite에 저장하고, 변경된 원문과 사례를 기존 PostgreSQL에 인계한다. 서로 다른 호스트의 작업은 완료되는 대로 다음 작업을 실행한다. 같은 호스트의 요청 간격은 공유한다. 종료 신호를 받으면 진행 중인 요청을 마친다. 비정상 종료 후에는 단일 실행 잠금을 확보한 다음 남은 작업을 회수한다.

Higherin은 인턴 또는 placement 원문, DEV는 공식 공개 API의 당사자 경험, 대학과 DAAD는 프로그램과 본인의 행동이 확인되는 보고서, GitHub는 공개 제안서 PDF를 대상으로 한다. 이름과 회차를 확인하지 못하면 미확인으로 보존한다. 참여 후기만으로 선정 결과나 유급 여부를 확정하지 않는다.

풀브라이트는 공개 WordPress API의 게시물 100개를 한 응답으로 받아 전문을 처리한다. 원본 JSON 배열과 각 문서의 `record_pointer`를 함께 보존한다. 제목과 본문에서 이름, 프로그램과 당사자의 서술을 확인하고, 한국어판과 영어판의 동일인 및 동일 회차를 합친다. API 게시물 전체를 경험 사례로 계산하지 않는다. Chevening은 공개 RSS의 `content:encoded` 전문을 저장하며, 발언자 귀속을 확인하기 전에는 `reference_material`로 둔다.

GitHub는 공식 저장소 압축 파일 API를 등록했다. 제공자의 요청 한도가 남아 있거나 초기화된 후 내려받고, PDF마다 기존 공개 Git tree의 blob SHA1과 대조한다. 압축 파일은 경로를 파일시스템에 풀지 않고 크기를 제한해 읽는다. DEV의 검색 태그는 GSoC 연도별 표현, MLH Fellowship, LFX Mentorship과 Google Season of Docs로 확대했으며 본문의 프로그램 연결을 따로 확인한다.

`Retry-After`, `X-RateLimit-Remaining`과 `X-RateLimit-Reset`을 보존한다. 실제 요청 한도 응답은 인증 거부와 구분한다. 한도가 초기화되면 실패 응답을 영구 캐시로 재사용하지 않고 다시 요청하며, 이전 실패 영수증도 보존한다. GitHub의 과거 12시간 보류는 공식 `GET /rate_limit`의 최신 응답으로 확인된 시각에 맞춰 조정한다. 대기 작업만 남아 있으면 서비스가 재개 시각까지 기다린다.

같은 DEV 작성자의 같은 프로그램과 같은 회차 글은 한 사례에 연결한다. 회차를 확인할 수 없는 동일 작성자의 글도 우선 한 미확인 군집에 둔다. 같은 URL과 정규화 본문이 중복되면 새로운 경험으로 추가하지 않는다. 기존 corpus와의 동일 URL 및 동일 본문 해시도 DB에서 조회한다. 다른 출처의 익명 작성자 동일성은 미확인이다.

기존 사례와 합쳐지는 새 자료는 해당 사례의 `document_ids`, 인용과 `supporting_collections`에 연결한다. 기존 선정 결과와 패턴 승인 상태는 변경하지 않는다.

`source_case_counts`는 원천별 고유 사례 수다. `sources.detail.accepted`는 처리한 URL 수이므로 사례 수와 다를 수 있다. `evidence_role_case_counts`는 한 사례가 여러 역할을 가질 수 있어 합산하지 않는다. 원문만 확보한 확인 대기 자료는 사례 수에서 제외한다.

원문과 전처리본은 공유 캐시의 `scale50k-20261109/experience-scale-v1`에 압축 보관한다. 과거 공개 사이트맵 스냅샷은 발견에만 재사용한다. 현재 원격 사이트맵 보류를 지우거나 새로 가져온 것처럼 표시하지 않는다. 각 상세 원문은 별도의 허용 경로와 본문 검사를 거친다.

상태 파일은 `status.json`, 영속 큐는 `exploration.sqlite`다. DB 원문은 `benefitlens.pipeline_document.metadata.experience_scale`, 사례는 `benefitlens.experience_discovery_case`의 `source_family=experience_scale_v1`로 조회한다. `active=true`와 `evidence_status=public_original_collected` 조건을 사용한다.

각 사례에는 원문 위치와 정확한 인용, 본문 해시, 문서 역할과 확인 상태를 보존한다. 분석 입력은 `model-inputs/episodes-full-text.jsonl.gz`의 사례별 JSONL이며 해당 사례에 연결된 문서 전문을 포함한다. 수집 완료 또는 별도 export 시 생성한 스냅샷이다. 수집 중 최신 건수와 시각이 다를 수 있다. 새 writing pattern은 자동 승인하지 않는다.

현재 로컬 실행 서비스는 `benefitlens-experience-scale.service`다.

API 조사 코드는 `tools/audit_benefitlens_experience_apis.py`이며, 원문 영수증은 공유 캐시의 `experience-api-audit-v1`에 저장한다. API 루트가 있어도 401, 403 또는 robots 제한이 있으면 그 경로를 사용하지 않는다. 원천별 확인 결과는 `pipeline/config/experience_api_audit.json`에 기록한다.

```bash
systemctl --user status benefitlens-experience-scale.service
journalctl --user -u benefitlens-experience-scale.service -n 10 --no-pager
```

목표에 도달해야 `complete`가 된다. 실행 한도나 후보 소진은 `checkpoint`로 기록하며, 이를 전체 웹의 경험 고갈로 해석하지 않는다. 현재 원천만으로 5만 달성이 보장되지는 않는다. 저자별 문구의 의미와 작성 활용 여부는 추가 검토 대상이다.
