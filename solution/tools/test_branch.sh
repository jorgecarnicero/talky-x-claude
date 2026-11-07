#!/usr/bin/env bash
# One command to test a branch on the LIVE simulation:  solution/tools/test_branch.sh <branch> [--sealed] [--force]
#                                                       solution/tools/test_branch.sh --merge <branch>
# Picks a Python that has pandas + duckdb (override with BANKREC_PY). Details: solution/tools/test_branch.py --help
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${BANKREC_PY:-}"
if [ -z "$PY" ]; then
  for c in "$HOME/hackdata/.venv/bin/python" python3; do
    if "$c" -c "import pandas, duckdb, pyarrow" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
[ -n "$PY" ] || { echo "need a Python with pandas, pyarrow and duckdb (set BANKREC_PY)"; exit 1; }
export BANKREC_PY="$PY" PYTHONHASHSEED=0
# --merge <branch> and <branch> --merge are both accepted
exec "$PY" "$HERE/test_branch.py" "$@"
