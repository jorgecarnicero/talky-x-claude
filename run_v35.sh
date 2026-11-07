#!/usr/bin/env bash
# v3.5 = our v3 (solution/bankrec.py) + extra high-precision passes (src/).   Run from the repo root:
#   ./run_v35.sh live      ->  sub_live_v35.csv.gz      (LIVE 91.92)
#   ./run_v35.sh final     ->  sub_final_v35.csv.gz     (validate, then upload ONCE)
# One-time setup:  python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r requirements-v35.txt
# Data layout: same as v3 (masters/ train/ live/ final/ starter/ at the repo root). Caches: solution/cache/v35/.
set -euo pipefail
PHASE=${1:?usage: ./run_v35.sh live|final}
cd "$(dirname "$0")"
PY=.venv/bin/python
case $PHASE in live) CHAIN="train live";; final) CHAIN="train live final";; *) echo "phase must be live|final"; exit 1;; esac
echo "== 1/4 v3 (solution/bankrec.py)"
python3 solution/bankrec.py --phase $PHASE --out sub_${PHASE}_v3.csv.gz
echo "== 2/4 remittance advices + PDF clauses ($CHAIN)"
for p in $CHAIN; do $PY src/docs_extract.py --phase $p; done
echo "== 3/4 v3.5 pipeline"
V3_SUB=sub_${PHASE}_v3.csv.gz $PY src/pipeline.py --phase $PHASE \
  --passes batches,remittance,ap_match,nonitem,v3import,ar_match,v3rest --out sub_${PHASE}_v35.csv.gz
echo "== 4/4 validate"
$PY src/validate_submission.py sub_${PHASE}_v35.csv.gz --phase $PHASE
python3 solution/tools/validate.py sub_${PHASE}_v35.csv.gz --phase $PHASE
