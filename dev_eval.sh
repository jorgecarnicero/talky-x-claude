#!/usr/bin/env bash
# Evaluate the v6c cascade on the tuning AND the held-out window at the same time (two processes), official scorer.
#   ./dev_eval.sh <tag> [extra env for the experiment, e.g. EXC_R7=1]
# Windows: TUNE 2024-10-01..2024-11-30 (tune here), HOLD 2025-02-01..2025-03-31 (confirm only; never tune on it).
# Needs backbone answers for each window: V3_TUNE=<csv> V3_HOLD=<csv> (team v3 output restricted to the window).
# Checkpoints (PIPE_CKPT=1) are on by default: experiments on passes after v3import skip the early passes.
set -euo pipefail
TAG=${1:?usage: ./dev_eval.sh <tag> [VAR=value ...]}; shift || true
cd "$(dirname "$0")"
if [ -z "${PY:-}" ]; then [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=.venv/Scripts/python.exe; fi
OUT=${OUT:-../out/dev}; mkdir -p "$OUT"
: "${V3_TUNE:?set V3_TUNE=<backbone csv for 2024-10..11>}" "${V3_HOLD:?set V3_HOLD=<backbone csv for 2025-02..03>}"
PASSES=${PASSES:-batches,remittance,ap_match,nonitem,ar_multi,ar_pdf,v3import,ar_match,ar_multi_late,ar_bigpool,v3rest,exc_fix,cancel_guard}
run() {  # window from to backbone
  env PIPE_CKPT=${PIPE_CKPT:-1} AR_DUST=1 EXC_R5=1 PAYER_DIR=1 "$@" true >/dev/null  # validate VAR=value syntax
}
one() {
  local W=$1 F=$2 T=$3 V3=$4; shift 4
  env PIPE_CKPT=${PIPE_CKPT:-1} AR_DUST=1 EXC_R5=1 PAYER_DIR=1 V3_SUB="$V3" "$@" \
    $PY src/pipeline.py --phase train --from $F --to $T --passes $PASSES \
    --out "$OUT/${TAG}_$W.csv.gz" --sources "$OUT/${TAG}_$W.src.csv" > "$OUT/${TAG}_$W.log" 2>&1
  $PY src/evaluate.py "$OUT/${TAG}_$W.csv.gz" --from $F --to $T --segments 90 > "$OUT/${TAG}_$W.score.txt" 2>&1
}
run "$@"
T0=$(date +%s)
one tune 2024-10-01 2024-11-30 "$V3_TUNE" "$@" & P1=$!
one hold 2025-02-01 2025-03-31 "$V3_HOLD" "$@" & P2=$!
wait $P1; wait $P2
for W in tune hold; do
  printf "%-5s " $W; grep -E '"score"' "$OUT/${TAG}_$W.score.txt" | tr -d ' ,\n'; printf "  "
  grep -E '"(allocation_f1|txn_accuracy|exception_macro_f1)"' "$OUT/${TAG}_$W.score.txt" | tr -d ' ,\n"'; echo
  grep -E "Trace|Error" "$OUT/${TAG}_$W.log" || true
done
echo "[$(( $(date +%s) - T0 ))s]  details: $OUT/${TAG}_{tune,hold}.score.txt"
