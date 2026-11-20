"""Continue the public search frontier with a bounded dispatch budget."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pipeline/src"))

from data_platform.dictionary_expansion import CodexBackend, Expansion, now


class BoundedExpansion(Expansion):
    def step(self, *, query_attempts: int, waves: int, wall_seconds: float, chunk_attempts: int | None = None):
        if query_attempts < 1 or waves < 1 or wall_seconds <= 0 or (chunk_attempts is not None and chunk_attempts < 0):
            raise ValueError("positive query, wave and time limits are required")
        deadline = time.monotonic() + wall_seconds

        def pending(table):
            return self.rows(f"SELECT count(*) n FROM {table} WHERE status='pending'")[0]["n"]

        def attempts():
            return self.rows("SELECT coalesce(sum(attempts),0) n FROM query")[0]["n"]

        def extract_remaining():
            if chunk_attempts is None:
                return self.config["extract_batch_size"]
            used = self.rows("SELECT coalesce(sum(attempts),0) n FROM chunk")[0]["n"]
            return chunk_attempts - used

        def finish(status):
            self.emit("step_finished", {"status": status, "query_attempts": attempts()})
            return self.export(status)

        with self.lock():
            if not self.rows("SELECT number FROM wave"):
                self.plan(1, "initial_seeds", self.dictionary())
            while not self.stopped and time.monotonic() < deadline:
                current = self.rows("SELECT * FROM wave ORDER BY number DESC LIMIT 1")[0]
                number = current["number"]
                if current["status"] == "closed":
                    previous = json.loads(current["payload"])
                    new = self.rows("SELECT id,term,lane FROM term WHERE wave=?", (number,))
                    if not new and previous["consecutive_zero_waves"] >= 2 and current["mode"] == "confirmation_rescan":
                        failures = any(self.rows(f"SELECT 1 FROM {t} WHERE status='failed' LIMIT 1") for t in ("query", "document", "chunk"))
                        return finish("frontier_exhausted_with_errors" if failures else "converged")
                    if number >= waves:
                        return finish("wave_limit")
                    if attempts() >= query_attempts:
                        return finish("query_attempt_limit")
                    self.plan(number + 1, "new_expressions" if new else "confirmation_rescan", new or self.dictionary())
                    continue
                # Drain work already discovered before reserving more queries.
                if pending("document"):
                    self.fetch_batch()
                elif pending("chunk") and extract_remaining() > 0:
                    batch_size = self.config["extract_batch_size"]
                    self.config["extract_batch_size"] = min(batch_size, extract_remaining())
                    try:
                        self.extract_batch()
                    finally:
                        self.config["extract_batch_size"] = batch_size
                elif pending("query"):
                    remaining = query_attempts - attempts()
                    if remaining <= 0:
                        return finish("query_attempt_limit")
                    batch_size = self.config["search_batch_size"]
                    self.config["search_batch_size"] = min(batch_size, remaining)
                    try:
                        self.search_batch()
                    finally:
                        self.config["search_batch_size"] = batch_size
                else:
                    if pending("chunk"):
                        return finish("chunk_attempt_limit")
                    older = self.rows("SELECT payload FROM wave WHERE number=?", (number - 1,))
                    found = self.rows("SELECT count(*) n FROM term WHERE wave=?", (number,))[0]["n"]
                    zero = 0 if found else (json.loads(older[0]["payload"])["consecutive_zero_waves"] if older else 0) + 1
                    metrics = {**json.loads(current["payload"]), "completed_at": now(), "new_terms": found,
                               "dictionary_terms": len(self.dictionary()), "consecutive_zero_waves": zero}
                    with self.connect() as conn:
                        conn.execute("UPDATE wave SET status='closed',payload=? WHERE number=?", (json.dumps(metrics), number))
                    self.emit("wave_completed", {"wave": number, **metrics})
                self.export("running")
            return finish("interrupted" if self.stopped else "time_limit")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--query-attempts", type=int, required=True, help="cumulative dispatches, including failures")
    p.add_argument("--waves", type=int, required=True, help="cumulative waves")
    p.add_argument("--wall-seconds", type=float, default=180, help="checked between stages; one in-flight call can finish")
    p.add_argument("--call-timeout", type=int, default=65)
    p.add_argument("--chunk-attempts", type=int, help="cumulative extraction dispatches; 0 allows search/fetch only")
    args = p.parse_args()
    if min(args.query_attempts, args.waves, args.wall_seconds, args.call_timeout) <= 0 or (args.chunk_attempts is not None and args.chunk_attempts < 0):
        p.error("query, wave and time limits must be positive; chunk limit cannot be negative")
    config = json.loads(args.config.read_text())
    backend = CodexBackend(args.output / "model-calls", command=os.environ.get("BENEFITLENS_CODEX_COMMAND", "codex"),
                           model=os.environ.get("BENEFITLENS_CODEX_MODEL", ""), timeout=args.call_timeout)
    run = BoundedExpansion(args.output, config, backend)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: setattr(run, "stopped", True))
    result = run.step(query_attempts=args.query_attempts, waves=args.waves, wall_seconds=args.wall_seconds,
                      chunk_attempts=args.chunk_attempts)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
