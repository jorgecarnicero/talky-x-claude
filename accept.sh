#!/usr/bin/env bash
# Merged acceptance decision (docs/PROTOCOL.md + the team's docs/ANTI_OVERFIT_team.md) for <new> against <base>.
#   ./accept.sh <base_tag> <new_tag>        (both already run with ./dev_eval.sh on tune + dev)
# All must hold:
#   T  TUNE (2024-10..11): composite delta >= +0.05 AND one-sided 95 % lower bound (payer-cluster bootstrap) > 0
#   G  DEV (2025-04..05): team gates G1-G3 (solution/tools/significance.py) = ACEPTAR
#   D  DEV composite: delta >= -0.02 AND > -1 SE (only PASS/FAIL + delta rounded to 0.01 are shown)
#   S  no TUNE segment with >= 100 lines loses > 2 accuracy points; no Trace / Error in the logs
# Each call counts one DEV look (counter in $OUT/dev_looks.txt). Then, only for an ACCEPT: ./sealed_check.sh (G4).
set -euo pipefail
B=${1:?base tag}; N=${2:?new tag}
cd "$(dirname "$0")"
if [ -z "${PY:-}" ]; then [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=.venv/Scripts/python.exe; fi
ROOT=$(cd .. && pwd)
export BANKREC_DATA=${BANKREC_DATA:-$ROOT/talky-x-claude} BANKREC_CACHE=${BANKREC_CACHE:-$ROOT/cache-v35} PYTHONWARNINGS=ignore
OUT=${OUT:-$ROOT/out/dev}
[ -d "$OUT/sig_dev" ] || $PY src/sigdata.py --from 2025-04-01 --to 2025-05-31 --out "$OUT/sig_dev" >/dev/null
T=$($PY src/compare.py "$OUT/${B}_tune.csv.gz" "$OUT/${N}_tune.csv.gz" --from 2024-10-01 --to 2024-11-30)
echo "TUNE  $T"
D=$($PY src/compare.py "$OUT/${B}_dev.csv.gz" "$OUT/${N}_dev.csv.gz" --from 2025-04-01 --to 2025-05-31)
set +e
$PY solution/tools/significance.py "$OUT/${B}_dev.csv.gz" "$OUT/${N}_dev.csv.gz" --data "$OUT/sig_dev" \
  --json "$OUT/${N}_dev.sig.json" > "$OUT/${N}_dev.sig.txt" 2>&1
SIG=$?
set -e
LOOKS=$(( $(cat "$OUT/dev_looks.txt" 2>/dev/null || echo 0) + 1 )); echo $LOOKS > "$OUT/dev_looks.txt"
$PY - "$T" "$D" "$SIG" "$OUT/${B}_tune.score.txt" "$OUT/${N}_tune.score.txt" "$LOOKS" "$OUT/${N}_dev.sig.txt" <<'EOF'
import re, sys
t, d, sig, sb, sn, looks, sigtxt = sys.argv[1:]
f = lambda s, k: float(re.search(k + r" ([+-]?[0-9.]+)", s).group(1))
td, tlo = f(t, "delta"), f(t, "lower")
dd, dse = f(d, "delta"), f(d, " se")
seg = lambda p: {m.group(1): (int(m.group(2)), float(m.group(3)))
                 for m in re.finditer(r"^\s+(\S+)\s+n=\s*(\d+) acc=([0-9.]+)", open(p, encoding="utf-8").read(), re.M)}
a, b = seg(sb), seg(sn)
drops = [(k, a[k][1], b[k][1]) for k in a if k in b and a[k][0] >= 100 and b[k][1] < a[k][1] - 0.02]
g1 = re.search(r"G1.*", open(sigtxt, encoding="utf-8", errors="replace").read())
ok_t = td >= 0.05 and tlo > 0
ok_g = sig == "0"
ok_d = dd >= -0.02 and dd > -dse
verdict = {"0": "ACEPTAR", "1": "NEUTRO", "2": "RECHAZAR"}.get(sig, "error " + sig)
print(f"T  TUNE composite  {'PASS' if ok_t else 'FAIL'}  delta {td:+.3f}, lower {tlo:+.3f}")
print(f"G  DEV team gates  {'PASS' if ok_g else 'FAIL'}  {verdict}   (details: {sigtxt})")
print(f"D  DEV composite   {'PASS' if ok_d else 'FAIL'}  delta {round(dd, 2):+.2f}   (DEV look #{looks})")
print("S  TUNE segments   " + ("PASS" if not drops else "FAIL " + ", ".join(f"{k} {x:.3f}->{y:.3f}" for k, x, y in drops)))
print("=> ACCEPT (next: ./sealed_check.sh, then G5 literal sweep)" if ok_t and ok_g and ok_d and not drops else "=> REJECT")
EOF
grep -lE "Trace|Error" "$OUT/${N}_tune.log" "$OUT/${N}_dev.log" && echo "=> REJECT (errors in logs)" || true
