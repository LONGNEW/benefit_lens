# 검색 표현 자동 확장

최초 24개 표현은 `pipeline/src/data_platform/exploration.py`의 `TERMS`에서 가져왔다. 과거 50개까지 늘어난 딕셔너리를 재사용하지 않고, 시작 표현부터 별도 SQLite에 실행한다. 공고와 경험 검색을 함께 다루며 기존 공고 DB와 발표용 10개 DB에는 쓰지 않는다.

## 실행

```bash
make dictionary-expand EXPANSION_OUTPUT=/absolute/path/to/run
```

동일한 출력 경로로 다시 실행하면 저장된 검색 결과와 처리 상태에서 이어간다. 로그인된 `codex` CLI가 필요하다. 검색과 LLM 처리는 해당 계정의 사용량을 소비한다. API 인증 토큰은 코드에서 읽지 않는다.

대화나 터미널에서 분리해 한 번 실행하려면 아래 명령을 사용한다. 종료 조건까지 반복한 뒤 종료하며, 진행 상태와 최종 결과를 같은 `RUN_REPORT.md`에 갱신한다.

```bash
.venv/bin/python tools/run_dictionary_expansion.py \
  --output /absolute/path/to/run --background
```

실행 PID와 로그 경로는 `process.json`에 기록한다. `kill -TERM PID`는 진행 중인 묶음을 마친 뒤 체크포인트를 저장하고 종료한다.

```bash
.venv/bin/python -m data_platform.dictionary_expansion \
  --output /absolute/path/to/run --status
```

접근 실패 등을 해결한 뒤 같은 경로에서 `--retry-failed`로 실패 항목을 다시 처리한다. 동시에 같은 경로를 실행하면 파일 잠금으로 두 번째 실행을 막는다. 설정을 바꾼 실험은 새 경로를 사용해야 한다.

## 한 라운드의 처리

1. 각 표현으로 검색문 두 개를 만들어 실제 웹 검색을 실행한다. 검색문을 바꾸지 않고 실행된 검색 이벤트가 있어야 완료로 인정한다.
2. 검색 이벤트가 반환한 상위 세 URL 중, 제목과 요약에서 명백히 무관하다고 판단된 자료를 제외하고 원문을 가져온다. 판단과 사유를 기록하고, 불확실한 자료는 수집한다. LLM의 최종 답변에만 등장한 URL은 수집하지 않는다. 사이트 내부 링크를 재귀적으로 따라가지 않는다.
3. HTML, PDF, HWP, HWPX의 텍스트를 저장하고 긴 텍스트는 겹침이 있는 청크로 나눠 전부 처리한다. 실행한 파일의 해시와 원문 경로를 보관한다.
4. LLM이 학생 개인의 지원과 관련된 재사용 가능한 표현을 제안하고, 기존 표현과 같은 검색 의도인지 판단한다. 프로그램 이름, 연도나 지역만 달라진 표현, 일반 연구책임자 연구비 등은 제외하도록 프롬프트에 명시한다.
5. Python이 표현과 최대 25단어의 인용이 해당 원문에 실제로 있는지 검증한다. 관련성은 LLM 판단, 원문 일치는 기계 검증으로 별도 기록한다.
6. 새 표현이 있으면 다음 라운드에서 그 표현을 검색한다. 새 표현이 없으면 전체 딕셔너리로 확인 검색을 수행한다.

## 종료 판정

연속 두 라운드에서 새 표현이 없고 마지막 라운드가 전체 딕셔너리 확인 검색이면 현재 검색 범위의 확장을 종료한다. 실패가 없으면 `converged`, 검색이나 원문, LLM 처리 실패가 남으면 `frontier_exhausted_with_errors`다. 후자의 기록으로 완전한 수렴을 주장하지 않는다.

검색 범위는 한국어와 영어, 설정한 검색문 두 개, 검색 결과 상위 세 URL이다. 웹 전체의 지원 기회가 소진됐다는 의미로 해석할 수 없다. `--max-queries`, `--max-waves`로 제한하면 각각 `query_limit`, `wave_limit`로 기록한다. 기본값 0은 제한을 두지 않는다.

## 결과 파일

| 파일 | 내용 |
| --- | --- |
| `status.json` | 현재 상태, 표현 수, 검색과 원문 및 청크 처리 수, 라운드별 증가량 |
| `dictionary.json` | 시작 표현, 새 표현, 원문 인용, 출처 URL, LLM 판단 |
| `expansion-graph.json` | 어떤 표현의 검색으로 어떤 원문과 새 표현을 발견했는지 연결 |
| `progress.jsonl` | 시간순 검색, 원문 수집, 표현 채택, 라운드 종료 기록 |
| `failures.json` | 실패 항목과 사유 |
| `expansion.sqlite` | 재시작을 위한 전체 상태와 후보의 채택 및 중복 판정 |
| `model-calls/` | 각 호출의 프롬프트, 스키마, 검색 이벤트, 구조화된 응답 |
| `raw/`, `text/` | 수집한 공개 원문과 변환 텍스트 |

## 기존 실행과의 관계

기존 `saturation_search.py`의 설명은 "The agent executes real web searches, saves responses, imports them here"다. 그 모듈은 외부 실행자가 저장한 검색 결과를 조정하는 방식이다. 새 `dictionary_expansion.py`는 검색 실행과 원문 수집, 표현 추출, 다음 검색 예약을 하나의 실행기로 연결한다. 이 결과는 검색 표현 실험이며, 공고 자격 조건의 검증이나 프로그램 DB 적재는 별도 단계다.

구현 참고: [Codex 비대화형 실행](https://learn.chatgpt.com/docs/non-interactive-mode), [설정 레퍼런스](https://learn.chatgpt.com/docs/config-file/config-reference).
