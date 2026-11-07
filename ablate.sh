#!/usr/bin/env bash
# Step 1 (simplify): ablation runs on TUNE only, one at a time, each compared with the baseline (payer bootstrap).
#   ./ablate.sh [list-file]   lines: "<tag> <VAR=value ...>"   ('#' comments). Results appended to $OUT/ablation.txt
# Later-pass ablations reuse the pipeline checkpoint (fast); ablating an early pass re-runs everything.
set -uo pipefail
cd "$(dirname "$0")"
ROOT=$(cd .. && pwd); OUT=${OUT:-$ROOT/out/dev}
if [ -z "${PY:-}" ]; then [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=.venv/Scripts/python.exe; fi
export BANKREC_DATA=${BANKREC_DATA:-$ROOT/talky-x-claude} BANKREC_CACHE=${BANKREC_CACHE:-$ROOT/cache-v35} PYTHONWARNINGS=ignore
ALL=batches,remittance,ap_match,nonitem,ar_multi,ar_pdf,v3import,ar_match,ar_multi_late,ar_bigpool,v3rest,exc_fix,cancel_guard
LIST=${1:-docs/ablation_list.txt}
grep -vE '^\s*(#|$)' "$LIST" | while read -r TAG ARGS; do
  [ -s "$OUT/ab_${TAG}_tune.csv.gz" ] && grep -q "^ab_${TAG} " "$OUT/ablation.txt" 2>/dev/null && continue
  if [[ $TAG == nopass_* ]]; then P=${TAG#nopass_}; PASSES=$(echo ",$ALL," | sed "s/,$P,/,/; s/^,//; s/,$//") \
      WINDOWS=tune ./dev_eval.sh "ab_$TAG" $ARGS > /dev/null
  else WINDOWS=tune ./dev_eval.sh "ab_$TAG" $ARGS > /dev/null; fi
  R=$($PY src/compare.py "$OUT/base_tune.csv.gz" "$OUT/ab_${TAG}_tune.csv.gz" --from 2024-10-01 --to 2024-11-30 --b 1000)
  E=$(grep -cE "Trace|Error" "$OUT/ab_${TAG}_tune.log")
  echo "ab_$TAG $ARGS | $R | errors=$E" | tee -a "$OUT/ablation.txt"
done
