#!/usr/bin/env bash
# Anti-overfitting harness: score an experiment on the TUNE and HOLD windows at the same time (two processes),
# with the official scorer.
#   ./dev_eval.sh <tag> [VAR=value ...]          TUNE + HOLD (default)
#   WINDOWS=tune ./dev_eval.sh <tag> [...]        TUNE only (fast iteration)
#   WINDOWS=gate ./dev_eval.sh <tag> [...]        GATE only: SEALED, run ONLY when claiming the goal is met
# Windows: TUNE 2024-10..11 (tune freely, per-segment details), HOLD 2025-02..03 (accept/reject only: totals only,
# no per-segment output is even written), GATE 2025-04..05 (sealed, totals only).
# Backbone answers (v3 = solution/bankrec.py --phase dev --months ...) default to $OUT/bb_<window>.csv.gz.
# Checkpoints (PIPE_CKPT=1) are on by default: experiments on passes after v3import skip the early passes.
set -euo pipefail
TAG=${1:?usage: ./dev_eval.sh <tag> [VAR=value ...]}; shift || true
cd "$(dirname "$0")"
if [ -z "${PY:-}" ]; then [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=.venv/Scripts/python.exe; fi
ROOT=$(cd .. && pwd)
export BANKREC_DATA=${BANKREC_DATA:-$ROOT/talky-x-claude} BANKREC_CACHE=${BANKREC_CACHE:-$ROOT/cache-v35} PYTHONWARNINGS=ignore
OUT=${OUT:-$ROOT/out/dev}; mkdir -p "$OUT"
WINDOWS=${WINDOWS:-tune hold}
PASSES=${PASSES:-batches,remittance,ap_match,nonitem,ar_multi,ar_pdf,v3import,ar_match,ar_multi_late,ar_bigpool,v3rest,exc_fix,cancel_guard}
DEFAULTS="PIPE_CKPT=${PIPE_CKPT:-1} AR_DUST=1 EXC_R5=1 PAYER_DIR=1"
env $DEFAULTS "$@" true >/dev/null  # validate VAR=value syntax
range() { case $1 in tune) echo "2024-10-01 2024-11-30";; hold) echo "2025-02-01 2025-03-31";; gate) echo "2025-04-01 2025-05-31";; *) echo "bad window $1" >&2; exit 1;; esac; }
one() {
  local W=$1; shift; local F T; read -r F T < <(range $W)
  local V3=$OUT/bb_$W.csv.gz; [ -s "$V3" ] || { echo "missing backbone $V3" >&2; return 1; }
  local SEG=0; [ "$W" = tune ] && SEG=200
  local S=$(date +%s)
  env $DEFAULTS V3_SUB="$V3" "$@" $PY src/pipeline.py --phase train --from $F --to $T --passes $PASSES \
    --out "$OUT/${TAG}_$W.csv.gz" --sources "$OUT/${TAG}_$W.src.csv" > "$OUT/${TAG}_$W.log" 2>&1 || true
  $PY src/evaluate.py "$OUT/${TAG}_$W.csv.gz" --from $F --to $T --segments $SEG > "$OUT/${TAG}_$W.score.txt" 2>&1 || true
  echo "$(( $(date +%s) - S ))" > "$OUT/${TAG}_$W.secs"
}
T0=$(date +%s); PIDS=""
for W in $WINDOWS; do one $W "$@" & PIDS="$PIDS $!"; done
for p in $PIDS; do wait $p; done
for W in $WINDOWS; do
  printf "%-4s %-5s " "$TAG" $W; grep -E '"score"' "$OUT/${TAG}_$W.score.txt" | tr -d ' ,\n'; printf "  "
  grep -E '"(allocation_f1|txn_accuracy|exception_macro_f1|allocation_precision|allocation_recall)"' "$OUT/${TAG}_$W.score.txt" | tr -d ' ,\n"'
  printf "  [%ss]\n" "$(cat "$OUT/${TAG}_$W.secs")"
  grep -E "Trace|Error" "$OUT/${TAG}_$W.log" "$OUT/${TAG}_$W.score.txt" || true
done
echo "[$(( $(date +%s) - T0 ))s]  details (TUNE only): $OUT/${TAG}_tune.score.txt"
