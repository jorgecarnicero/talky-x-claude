#!/usr/bin/env bash
# v8b-pf (LIVE 94.67): v3import_strong (v3's referenced/single-exact answers) -> our matchers -> ar_guess (calibrated best
# guess for receipts v3 leaves unanswered) -> v3import/v3rest -> exc_fix -> cancel_guard, then src/postfix.py.
#   ./run_v8b_pf.sh live|final   -> sub_<phase>_v8b_pf.csv.gz  (and _pf130 with a 130-day postfix window)
set -euo pipefail
PHASE=${1:?usage: ./run_v8b_pf.sh live|final}
cd "$(dirname "$0")"; PY=${PY:-.venv/bin/python}
case $PHASE in live) CHAIN="train live";; final) CHAIN="train live final";; *) echo "phase must be live|final"; exit 1;; esac
if [ -z "${V3_BACKBONE:-}" ]; then python3 solution/bankrec.py --phase $PHASE --out sub_${PHASE}_v3.csv.gz; V3_BACKBONE=sub_${PHASE}_v3.csv.gz; fi
for p in $CHAIN; do $PY src/docs_extract.py --phase $p; done
AR_GUESS=1 AR_DUST=1 EXC_R5=1 PAYER_DIR=1 V3_SUB=$V3_BACKBONE $PY src/pipeline.py --phase $PHASE \
  --passes batches,remittance,ap_match,nonitem,ar_multi,ar_pdf,v3import_strong,ar_match,ar_multi_late,ar_bigpool,ar_dispute,ar_guess,v3import,v3rest,exc_fix,cancel_guard \
  --out sub_${PHASE}_v8b.csv.gz
$PY src/postfix.py sub_${PHASE}_v8b.csv.gz --phase $PHASE --out sub_${PHASE}_v8b_pf.csv.gz
$PY src/postfix.py sub_${PHASE}_v8b.csv.gz --phase $PHASE --win 130 --out sub_${PHASE}_v8b_pf130.csv.gz
for f in sub_${PHASE}_v8b_pf sub_${PHASE}_v8b_pf130; do $PY src/validate_submission.py $f.csv.gz --phase $PHASE; done
