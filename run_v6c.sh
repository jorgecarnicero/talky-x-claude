#!/usr/bin/env bash
# v6c = v6b (LIVE 93.31) with identical output, faster:
#   * perf patch in batches (index from Python lists, lazy groups)            -> batch pass ~36 % faster
#   * parallel chain: backbone (solution/bankrec.py) || docs extraction, then the cascade, whose v3import pass
#     waits for the backbone ("<file>.done" marker) while the first six passes already run.
#   ./run_v6c.sh live | final      -> sub_<phase>_v6c.csv.gz (validated)
# Env: PY (pipeline python, default .venv/bin/python or .venv/Scripts/python.exe), PY3 (backbone python, default python3),
#      V3_BACKBONE=<csv> to reuse an existing backbone output.
set -euo pipefail
PHASE=${1:?usage: ./run_v6c.sh live|final}
cd "$(dirname "$0")"
if [ -z "${PY:-}" ]; then [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=.venv/Scripts/python.exe; fi
PY3=${PY3:-python3}
case $PHASE in live) CHAIN="train live";; final) CHAIN="train live final";; *) echo "phase must be live|final"; exit 1;; esac
T0=$(date +%s)
if [ -z "${V3_BACKBONE:-}" ]; then
  V3_BACKBONE=sub_${PHASE}_v3.csv.gz
  rm -f "$V3_BACKBONE.done"
  echo "== 1/3 v3 backbone in background -> $V3_BACKBONE"
  ( $PY3 solution/bankrec.py ${BANKREC_DATA:+--data "$BANKREC_DATA"} --phase $PHASE --out "$V3_BACKBONE" > "backbone_${PHASE}.log" 2>&1 \
      && touch "$V3_BACKBONE.done" || { echo "BACKBONE FAILED, see backbone_${PHASE}.log"; exit 1; } ) &
  BB_PID=$!
else
  touch "$V3_BACKBONE.done"; BB_PID=""
fi
echo "== 2/3 remittance advices + PDF clauses ($CHAIN; cached phases are skipped)"
for p in $CHAIN; do $PY src/docs_extract.py --phase $p; done
echo "== 3/3 cascade (v3import waits for the backbone)  [$(( $(date +%s) - T0 ))s]"
V3_WAIT=1 AR_DUST=1 EXC_R5=1 PAYER_DIR=1 V3_SUB=$V3_BACKBONE $PY src/pipeline.py --phase $PHASE \
  --passes batches,remittance,ap_match,nonitem,ar_multi,ar_pdf,v3import,ar_match,ar_multi_late,ar_bigpool,v3rest,exc_fix,cancel_guard \
  --out sub_${PHASE}_v6c.csv.gz --sources sub_${PHASE}_v6c_src.csv
[ -n "$BB_PID" ] && wait $BB_PID
echo "== validate  [$(( $(date +%s) - T0 ))s total]"
$PY src/validate_submission.py sub_${PHASE}_v6c.csv.gz --phase $PHASE
