#!/usr/bin/env bash
# v7d-pf (LIVE 94.25): v7c code with our matchers BEFORE v3import, then the best-guess post-processor (src/postfix.py)
# on receipts left UNIDENTIFIED.   ./run_v7d_pf.sh live|final  -> sub_<phase>_v7d_pf.csv.gz (validated)
set -euo pipefail
PHASE=${1:?usage: ./run_v7d_pf.sh live|final}
cd "$(dirname "$0")"
PY=${PY:-.venv/bin/python}
case $PHASE in live) CHAIN="train live";; final) CHAIN="train live final";; *) echo "phase must be live|final"; exit 1;; esac
if [ -z "${V3_BACKBONE:-}" ]; then
  echo "== 1/5 v3 backbone"; python3 solution/bankrec.py --phase $PHASE --out sub_${PHASE}_v3.csv.gz; V3_BACKBONE=sub_${PHASE}_v3.csv.gz
fi
echo "== 2/5 docs"; for p in $CHAIN; do $PY src/docs_extract.py --phase $p; done
echo "== 3/5 pipeline (matchers before v3import)"
AR_DUST=1 EXC_R5=1 PAYER_DIR=1 V3_SUB=$V3_BACKBONE $PY src/pipeline.py --phase $PHASE \
  --passes batches,remittance,ap_match,nonitem,ar_multi,ar_pdf,ar_match,ar_multi_late,ar_bigpool,ar_dispute,v3import,v3rest,exc_fix,cancel_guard \
  --out sub_${PHASE}_v7d.csv.gz
echo "== 4/5 best-guess post-processor"; $PY src/postfix.py sub_${PHASE}_v7d.csv.gz --phase $PHASE --out sub_${PHASE}_v7d_pf.csv.gz
echo "== 5/5 validate"; $PY src/validate_submission.py sub_${PHASE}_v7d_pf.csv.gz --phase $PHASE
