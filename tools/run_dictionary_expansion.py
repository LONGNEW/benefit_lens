"""Launch the one-off expansion, optionally detached, and maintain a short report."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import traceback

from data_platform.dictionary_expansion import CodexBackend, Expansion, ROOT, write_json


def report(run, status, state):
    labels = {"running": "자동 실행 중", "converged": "설정한 검색 범위에서 수렴",
              "frontier_exhausted_with_errors": "확장 큐 처리 종료, 실패 항목 남음",
              "interrupted": "체크포인트 저장 후 중단", "runner_error": "실행기 오류",
              "query_limit": "검색 수 제한 도달", "wave_limit": "라운드 제한 도달"}
    counts = state["counters"]
    distinct_queries = run.rows("SELECT count(DISTINCT q) n FROM query WHERE status='executed'")[0]["n"]
    hashes = run.rows("SELECT count(DISTINCT json_extract(payload,'$.content_sha256')) n FROM document WHERE status='collected'")[0]["n"]
    rows = ["# 검색 표현 자동 확장 실행 기록", "", "상태: " + labels.get(status, status),
            "갱신 시각: " + state["updated_at"], "",
            f"시작 표현 {state['seed_terms']}개에서 현재 {state['dictionary_terms']}개로 확장했다. 신규 자동 채택 표현은 {state['new_terms']}개다.", "",
            "| 처리 항목 | 수 |", "| --- | ---: |",
            f"| 실행 완료 검색문, 확인 라운드의 반복 포함 | {counts['query'].get('executed', 0)} |",
            f"| 서로 다른 실행 검색문 | {distinct_queries} |",
            f"| 수집한 URL | {counts['document'].get('collected', 0)} |",
            f"| 수집 콘텐츠의 서로 다른 SHA256 | {hashes} |",
            f"| LLM 처리 완료 텍스트 청크 | {counts['chunk'].get('processed', 0)} |",
            f"| 명백한 범위 밖 URL | {counts['document'].get('excluded_scope', 0)} |",
            f"| 대기 검색, 원문, 청크 | {counts['query'].get('pending', 0)}, {counts['document'].get('pending', 0)}, {counts['chunk'].get('pending', 0)} |",
            f"| 실패 검색, 원문, 청크 | {counts['query'].get('failed', 0)}, {counts['document'].get('failed', 0)}, {counts['chunk'].get('failed', 0)} |", "",
            "| 라운드 | 방식 | 상태 | 새 표현 |", "| --- | --- | --- | ---: |"]
    modes = {"initial_seeds": "최초 딕셔너리", "new_expressions": "새 표현 검색", "confirmation_rescan": "전체 딕셔너리 확인 검색"}
    for wave in state["waves"][-12:]:
        rows.append(f"| {wave['number']} | {modes.get(wave['mode'], wave['mode'])} | {wave['status']} | {wave['payload'].get('new_terms', '처리 중')} |")
    rows += ["", "## 신규 표현 예시", "", "| 표현 | 용도 | 출처 |", "| --- | --- | --- |"]
    # Show source diversity while the complete dictionary remains available below.
    terms = run.rows("SELECT term,lane,payload FROM term WHERE seed=0 ORDER BY wave,rowid")
    picked, used = [], set()
    for term in terms:
        data = json.loads(term["payload"])
        if data["source_url"] not in used:
            picked.append((term, data))
            used.add(data["source_url"])
        if len(picked) == 10:
            break
    for term, data in picked:
        expression = term["term"].replace("|", "\\|").replace("\u00b7", ",")
        rows.append(f"| {expression} | {'공고' if term['lane'] == 'announcement' else '지원 자료와 경험'} | [원문]({data['source_url']}) |")
    rows += ["", "새 표현의 관련성과 신규성은 LLM이 판단하고, Python은 유니코드와 대소문자, 공백을 정규화해 표현과 인용의 원문 일치를 검사한다.",
             "각 표현의 원문 인용, 문서 해시와 검색 연결은 아래 전체 딕셔너리와 그래프에서 확인할 수 있다.", "",
             "## 반복과 종료 기준", "",
             "새 표현을 검색하고 원문을 수집해 다시 표현을 추출한다. 새 표현이 없는 라운드 뒤에는 전체 딕셔너리로 확인 검색한다. 연속 두 라운드의 신규 표현이 0이고 마지막이 확인 검색이면 확장 큐 처리를 끝낸다. 실패가 남으면 오류가 남은 종료 상태로 구분한다.",
             "종료 판정 범위는 한국어와 영어, 표현별 검색문 두 개, 각 검색 이벤트의 첫 세 URL이다. 기존 공고 DB와 발표용 10개 데모 DB에는 적재하지 않는다.", "",
             "## 전체 기록", ""]
    for name in ["status.json", "dictionary.json", "expansion-graph.json", "failures.json", "progress.jsonl", "config.json"]:
        rows.append(f"- [{name}]({run.folder / name})")
    text = "\n".join(rows) + "\n"
    if len(text) > 10000:
        raise ValueError("Run report exceeds document character limit")
    target = run.folder / "RUN_REPORT.md"
    temporary = target.with_suffix(".md.tmp")
    temporary.write_text(text)
    temporary.replace(target)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "dictionary_expansion.json")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--max-queries", type=int, default=0)
    parser.add_argument("--max-waves", type=int, default=0)
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    if args.background:
        command = [sys.executable, str(Path(__file__).resolve()), "--output", str(folder),
                   "--config", str(args.config.resolve()), "--max-queries", str(args.max_queries),
                   "--max-waves", str(args.max_waves)]
        if args.retry_failed:
            command.append("--retry-failed")
        with (folder / "runner.log").open("a") as log:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True, cwd=ROOT.parent)
        write_json(folder / "process.json", {"pid": child.pid, "launched_at": datetime.now().astimezone().isoformat(),
                                             "log": str(folder / "runner.log"), "report": str(folder / "RUN_REPORT.md")})
        print(json.dumps({"pid": child.pid, "report": str(folder / "RUN_REPORT.md")}, ensure_ascii=False))
        return
    config = json.loads(args.config.read_text())
    backend = CodexBackend(folder / "model-calls", command=os.environ.get("BENEFITLENS_CODEX_COMMAND", "codex"),
                           model=os.environ.get("BENEFITLENS_CODEX_MODEL", ""),
                           vocabulary_scope=config.get("vocabulary_scope"))
    run = Expansion(folder, config, backend)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: setattr(run, "stopped", True))
    run.export_callback = lambda state: report(run, state["status"], state)
    try:
        run.run(max_queries=args.max_queries, max_waves=args.max_waves, retry_failed=args.retry_failed)
    except BlockingIOError:
        raise SystemExit("Another runner already owns this output folder.")
    except Exception:
        traceback.print_exc()
        run.export("runner_error")
        raise


if __name__ == "__main__":
    main()
