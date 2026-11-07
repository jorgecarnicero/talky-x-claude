#!/usr/bin/env bash
# Pre-registered acceptance decision (docs/PROTOCOL.md) for experiment <new> against reference <base>.
#   ./accept.sh <base_tag> <new_tag>        (both already run with ./dev_eval.sh)
# TUNE: delta >= +0.05 AND one-sided 95 % lower bound (payer-cluster paired bootstrap) > 0.
# HOLD: delta >= -0.02 AND delta > -1 SE. Only a PASS/FAIL bit + the delta rounded to 0.01 is shown; every call
#       counts as one HOLD look (budget 40, counter in $OUT/hold_looks.txt).
# Segments: no TUNE segment with >= 100 lines loses > 2 accuracy points. Logs: no Trace / Error.
set -euo pipefail
B=${1:?base tag}; N=${2:?new tag}
cd "$(dirname "$0")"
if [ -z "${PY:-}" ]; then [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=.venv/Scripts/python.exe; fi
ROOT=$(cd .. && pwd)
export BANKREC_DATA=${BANKREC_DATA:-$ROOT/talky-x-claude} BANKREC_CACHE=${BANKREC_CACHE:-$ROOT/cache-v35} PYTHONWARNINGS=ignore
OUT=${OUT:-$ROOT/out/dev}
T=$($PY src/compare.py "$OUT/${B}_tune.csv.gz" "$OUT/${N}_tune.csv.gz" --from 2024-10-01 --to 2024-11-30)
echo "TUNE  $T"
H=$($PY src/compare.py "$OUT/${B}_hold.csv.gz" "$OUT/${N}_hold.csv.gz" --from 2025-02-01 --to 2025-03-31)
LOOKS=$(( $(cat "$OUT/hold_looks.txt" 2>/dev/null || echo 0) + 1 )); echo $LOOKS > "$OUT/hold_looks.txt"
$PY - "$T" "$H" "$OUT/${B}_tune.score.txt" "$OUT/${N}_tune.score.txt" "$LOOKS" <<'EOF'
import re, sys
t, h, sb, sn, looks = sys.argv[1:]
f = lambda s, k: float(re.search(k + r" ([+-]?[0-9.]+)", s).group(1))
td, tlo = f(t, "delta"), f(t, "lower")
hd, hse = f(h, "delta"), f(h, " se")
seg = lambda p: {m.group(1): (int(m.group(2)), float(m.group(3)))
                 for m in re.finditer(r"^\s+(\S+)\s+n=\s*(\d+) acc=([0-9.]+)", open(p, encoding="utf-8").read(), re.M)}
a, b = seg(sb), seg(sn)
drops = [(k, a[k][1], b[k][1]) for k in a if k in b and a[k][0] >= 100 and b[k][1] < a[k][1] - 0.02]
ok_t = td >= 0.05 and tlo > 0
ok_h = hd >= -0.02 and hd > -hse
print(f"TUNE  {'PASS' if ok_t else 'FAIL'}  delta {td:+.3f}, lower {tlo:+.3f}")
print(f"HOLD  {'PASS' if ok_h else 'FAIL'}  delta {round(hd, 2):+.2f}   (HOLD look #{looks} of 40)")
print("SEGS  " + ("PASS" if not drops else "FAIL " + ", ".join(f"{k} {x:.3f}->{y:.3f}" for k, x, y in drops)))
print("=> ACCEPT" if ok_t and ok_h and not drops else "=> REJECT")
EOF
grep -lE "Trace|Error" "$OUT/${N}_tune.log" "$OUT/${N}_hold.log" && echo "=> REJECT (errors in logs)" || true
