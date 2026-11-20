#!/usr/bin/env bash
set -euo pipefail
tools_dir="$(cd "$(dirname "$0")" && pwd)"
python_bin="${BENEFITLENS_PYTHON:-/usr/bin/python3}"
company_data="${BENEFITLENS_COMPANY_OUTPUT:-/home/longnew/.cache/datasets/benefitlens/scale50k-20261109/korea-company-expansion-v1}"
export BENEFITLENS_COMPANY_OUTPUT="$company_data"
export BENEFITLENS_PLAYWRIGHT="${BENEFITLENS_PLAYWRIGHT:-/home/longnew/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright}"
export PYTHONPATH="$tools_dir${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$company_data"
exec 9>"$company_data/company.lock"
flock -n 9

"$python_bin" "$tools_dir/collect_benefitlens_company_http.py"
node "$tools_dir/collect_benefitlens_company_browser.cjs" |
    "$python_bin" "$tools_dir/archive_benefitlens_company_browser.py"
