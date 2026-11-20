# 고정 원형과 증분으로 확장하기

2026-11-09 구현. [팀 인계 계약](team-handoff.md)의 원형 파일을 복사하지 않고, 새 현재 공고와 공통 표현, 과거 경험을 작은 증분 파일로 연결한다. 실행기는 [run_benefitlens_expansion.py](../../tools/run_benefitlens_expansion.py)다.

## 입력 manifest

contract_version은 expansion-manifest-v1, as_of는 확인 기준일이다. 파일 참조는 절대 path와 물리 파일의 sha256을 포함한다. 허용되지 않은 최상위 필드나 달라진 파일 해시는 오류다.

| 필드 | 역할 |
| --- | --- |
| baseline | 기존 team-handoff-v1 원형 |
| baseline_approval_patch | 정확한 원형에 결속된 기존 승인 기록 |
| enrichments | 이전 검토를 유지할 공통 매핑, 검토자와 고정 검토 기록 |
| semantic_patch | 개별 원문을 대조한 추가 분류와 지원 정보, 자동 규칙의 후보 목록 참조 |
| semantic_patches | 새 수집 원문에 대한 추가 패치 목록, 병합 후 순서대로 적용 |
| deltas | 현재 공고 또는 과거 작성용 인계 bundle |
| required_fields | 문항별 필요한 사용자 정보 |
| audit_inputs | 검토 기록과 실행 근거의 고정 파일 |
| comparison_manifest | 같은 기준일의 직전 검증 manifest, 이번 신규 집계의 비교 기준 |

deltas.role은 current_opportunities 또는 historical_writing이다. addition_kind는 new와 prior_validated를 구분한다. 이전 NVIDIA 사례를 가져오는 것과 새 NSF GRFP 및 Fulbright 경험을 추가하는 것을 따로 집계할 수 있다. 상대 마감이 아직 지원되지 않는 대출 후보는 현재 공고 입력에 넣지 않는다.

```bash
python3 tools/run_benefitlens_expansion.py \
  --manifest /absolute/path/manifest.json \
  --state /home/longnew/.cache/datasets/benefitlens/expansion-20261109/integration
```

조정기는 HTTP 요청이나 모델 추출을 수행하지 않는다. 수집 어댑터가 남긴 확인 일자와 원문을 검증한다. 새 수집은 [expand_benefitlens_sources.py](../../tools/expand_benefitlens_sources.py)가 공개 URL의 원문과 추출 텍스트, 상태를 보존하며, 이번 출처별 필드 매핑과 인계 조립 절차는 결과 보고의 재현 스크립트를 따른다.

## 무엇을 신규 확보로 세는가

현재 공고 입력은 같은 기준일의 접수 상태가 open이어야 한다. 학생 대상 범위, 접수 상태와 기간 근거의 승인 지문이 유효하고 값도 긍정이어야 한다. 새 모집 변형의 기간은 원래 HTML이나 공식 응답에서 다시 검증한다. 개인의 전체 자격 충족 여부와는 구분한다. 한국어의 명시적 연월일과 점으로 구분한 날짜를 처리하며, 연도가 생략된 마감일은 바로 연결된 같은 연도 구간에서만 보완한다. 수정일, 게시일이나 실행일에서 연도를 가져오지 않는다. Jobbnorge의 deadlineOn은 해당 공고의 공식 JSON ID와 요청 ID를 맞추고, 실제 공식 화면 템플릿에서 신청 마감 라벨과 이 필드의 연결을 검증한다. JSON 필드 이름을 원문 인용처럼 꾸미거나 게시 종료일로 대신하지 않는다.

기관과 직무 그룹화는 원형과 새 증분의 source_specific를 합쳐 같은 규칙으로 실행한다. 위치와 기간의 변형은 보존하고 부모 후보 ID의 집합으로 중복을 줄인다. 유사한 잔여 직무는 검토 대상으로 남긴다. 모집 변형 하나를 상충하는 부모 둘에 연결할 수 없다.

comparison_manifest를 제공하면 신규 모집 변형과 신규 부모 후보를 직전 검증본과의 차집합으로 계산한다. 생략하면 원형이 비교 기준이다. 비교본은 같은 확인 기준일과 같은 복원 원형을 사용해야 하며, 이전 행과 원문, 작성 입력 요건을 누락하거나 바꾸면 실행을 거절한다. 원형 이후 추가된 모든 현재 모집 변형의 접수 기간을 다시 검증하되, 이번 신규 수량과 전체 증분 재검증 수량은 구분한다. 경험 추가, 유형 수정과 같은 자료 재수집을 새 현재 공고로 세지 않는다. 과거 작성용 회차가 기존 현재 회차를 덮어쓰는 것도 거절한다. 부모 후보는 규칙 기반 집계이며 의미적으로 전수 확인한 고유 기회 수는 null로 유지한다.

## 공통 표현과 작성 자료의 확장

[benefitlens_semantic_expansion.py](../../tools/benefitlens_semantic_expansion.py)는 전체 원천에 자동 분류 후보를 제안한다. 개별 원문 대조 기록이 있는 항목만 승인된 분류와 지원 정보 claim으로 추가한다. 기존 SQL program.opportunity_type 값을 일괄 변경하지 않으며, 검토한 분류는 advice/opportunity_classification의 JSON 값과 metadata.normalized_classification_overlay로 조회한다.

기존 원형과 claim, 문서 해시는 유지한다. 공통 금액의 한도와 범위, 지원 목적과 지급 방식을 구분한다. 같은 지원금을 학비와 생활비에 모두 쓸 수 있다는 설명은 공유 용도 집합으로 저장하고 지급액을 두 번 더하지 않는다.

과거 제출문과 인터뷰는 자료 역할, 인물과 회차, 최종 제출 여부를 보존한다. 실제 LLM으로 경험 전체를 분석한 결과도 검토 전에는 별도 후보 receipt로 둔다. 새 후보 수와 승인 패턴 수는 각각 보고한다. 사용자 사실이 없으면 초안 문장은 null이고 필요한 정보를 질문한다.

## 실행 결과와 보존

최종 결과는 고정 manifest, 전체 인계의 검증 결과, 변경한 프로그램을 고른 SQL 검증 표본과 재실행 기록으로 나뉜다. 전체 원형과 증분을 결합한 결과는 메모리에서 검증하고 작은 감사 결과만 저장한다. SQL 검증 표본은 별도의 고정 입력으로 보존하고 격리된 PostgreSQL에 적재한다.

같은 manifest를 다시 실행할 때 원문과 승인 지문, 저장 결과의 해시를 다시 확인한다. 스키마와 파서, 그룹화 및 조정기 코드의 버전도 기록한다. 현재 규모와 출처별 제한은 [범위 추가 확장 결과](../../proposal/결과물/범위추가확장_2026-11-09/README.md)를 따른다. [직전 증분 확장](../../proposal/결과물/증분확장_2026-11-09/README.md)도 보존한다. 정기 실행 스케줄과 제품 화면 연결은 이 조정기에 포함하지 않았다.
