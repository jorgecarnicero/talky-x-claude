#!/usr/bin/env bash
# v9-final = v8c-pf130 (run_v8b_pf.sh chain + ZA01 payer pattern) + exc_overpay (EXC_OVERPAY=1, LIVE +0.18 on v7) + FINAL guards.
#   ./run_v9_final.sh live|final   -> sub_<phase>_v9_pf130.csv.gz
set -euo pipefail
PHASE=${1:?usage: ./run_v9_final.sh live|final}
cd "$(dirname "$0")"; PY=${PY:-.venv/bin/python}
export PYTHONHASHSEED=0
case $PHASE in live) CHAIN="train live";; final) CHAIN="train live final";; *) echo "phase must be live|final"; exit 1;; esac
if [ -z "${V3_BACKBONE:-}" ]; then python3 solution/bankrec.py --phase $PHASE --out sub_${PHASE}_v3.csv.gz; V3_BACKBONE=sub_${PHASE}_v3.csv.gz; fi
for p in $CHAIN; do $PY src/docs_extract.py --phase $p; done
AR_GUESS=1 AR_DUST=1 EXC_R5=1 PAYER_DIR=1 EXC_OVERPAY=1 V3_SUB=$V3_BACKBONE $PY src/pipeline.py --phase $PHASE \
  --passes batches,remittance,ap_match,nonitem,ar_multi,ar_pdf,v3import_strong,ar_match,ar_multi_late,ar_bigpool,ar_dispute,ar_guess,v3import,v3rest,exc_fix,exc_overpay,cancel_guard \
  --out sub_${PHASE}_v9.csv.gz --sources sub_${PHASE}_v9_src.csv
$PY src/postfix.py sub_${PHASE}_v9.csv.gz --phase $PHASE --win 130 --out sub_${PHASE}_v9_pf130.csv.gz
$PY src/validate_submission.py sub_${PHASE}_v9_pf130.csv.gz --phase $PHASE
