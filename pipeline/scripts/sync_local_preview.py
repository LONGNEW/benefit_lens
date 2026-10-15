"""One-shot local D1 writer. Token stays in child memory and is then discarded."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from urllib.request import urlopen
from sync_d1 import read_opportunities, sync_resource


def copy_school_coverage(web: Path) -> None:
    """Mirror the generated public coverage report, without profile data."""
    source = web.parent / "pipeline/data/exports/school_coverage.json"
    if not source.exists():
        return
    report = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(report.get("departments"), list) or not isinstance(report.get("checkedAt"), str):
        raise ValueError("invalid generated school coverage report")
    shutil.copyfile(source, web / "public/school-coverage.json")
    built = web / "dist/client/school-coverage.json"
    if built.exists():
        shutil.copyfile(source, built)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True)
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    web = Path(__file__).resolve().parents[2] / "web"
    copy_school_coverage(web)
    token = secrets.token_urlsafe(32)
    # Do not forward unrelated account or API credentials into the worker.
    environment = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "XDG_CONFIG_HOME", "LANG"}}
    environment.update(D1_SYNC_TOKEN=token, CLOUDFLARE_INCLUDE_PROCESS_ENV="true", WRANGLER_SEND_METRICS="false", WRANGLER_WRITE_LOGS="false")
    process = subprocess.Popen([args.node, "node_modules/wrangler/bin/wrangler.js", "dev", "--config", "dist/server/wrangler.json", "--local", "--persist-to", ".wrangler/state", "--ip", "127.0.0.1", "--port", str(args.port), "--inspector-port", "0", "--env-file", "/dev/null", "--log-level", "none"], cwd=web, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    base_url = f"http://127.0.0.1:{args.port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("temporary local writer exited")
            try:
                with urlopen(base_url + "/api/opportunities?fundingTarget=all", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("temporary local writer did not become ready")
        sync_resource(base_url, token, "opportunities", read_opportunities("candidate"), 40)
    finally:
        import signal
        os.killpg(process.pid, signal.SIGTERM) if process.poll() is None else None
        process.wait(timeout=10)


if __name__ == "__main__":
    main()
