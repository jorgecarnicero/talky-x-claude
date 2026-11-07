#!/usr/bin/env bash
# Team gate G4 (docs/ANTI_OVERFIT_team.md): sealed window 2025-01..02, ONE run per candidate that already passed
# ./accept.sh. Prints only the SIGN of the change (composite score and net lines fixed); never a score, a segment or
# a line. The candidate is rejected if the sign is not the same as on DEV. Each call is logged in $OUT/sealed_runs.txt.
#   ./sealed_check.sh <base_tag> <new_tag> [VAR=value ...]   (the flags that define <new_tag>)
set -euo pipefail
B=${1:?base tag}; N=${2:?new tag}; shift 2
cd "$(dirname "$0")"
if [ -z "${PY:-}" ]; then [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=.venv/Scripts/python.exe; fi
ROOT=$(cd .. && pwd)
export BANKREC_DATA=${BANKREC_DATA:-$ROOT/talky-x-claude} BANKREC_CACHE=${BANKREC_CACHE:-$ROOT/cache-v35} PYTHONWARNINGS=ignore
OUT=${OUT:-$ROOT/out/dev}
grep -q "^$N " "$OUT/sealed_runs.txt" 2>/dev/null && { echo "$N already used its sealed run"; exit 1; }
[ -d "$OUT/sig_sealed" ] || $PY src/sigdata.py --from 2025-01-01 --to 2025-02-28 --out "$OUT/sig_sealed" >/dev/null
[ -s "$OUT/${B}_sealed.csv.gz" ] || WINDOWS=sealed ./dev_eval.sh "$B" >/dev/null
WINDOWS=sealed ./dev_eval.sh "$N" "$@" >/dev/null
echo "$N $(date +%F_%T) base=$B" >> "$OUT/sealed_runs.txt"
D=$($PY src/compare.py "$OUT/${B}_sealed.csv.gz" "$OUT/${N}_sealed.csv.gz" --from 2025-01-01 --to 2025-02-28 --b 200)
set +e
$PY solution/tools/significance.py "$OUT/${B}_sealed.csv.gz" "$OUT/${N}_sealed.csv.gz" --data "$OUT/sig_sealed" \
  --json "$OUT/${N}_sealed.sig.json" > /dev/null 2>&1
set -e
$PY - "$D" "$OUT/${N}_sealed.sig.json" <<'EOF'
import json, re, sys
d = float(re.search(r"delta ([+-]?[0-9.]+)", sys.argv[1]).group(1))
sign = lambda x: "improves" if x > 0 else ("ties" if x == 0 else "WORSENS")
try:
    j = json.load(open(sys.argv[2], encoding="utf-8"))
    net = j.get("G1", {}).get("c", 0) - j.get("G1", {}).get("b", 0)
    lines = sign(net)
except Exception:
    lines = "n/a"
print(f"SEALED (G4, sign only): composite {sign(d)}, net lines {lines}")
EOF
