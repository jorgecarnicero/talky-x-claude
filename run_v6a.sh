#!/usr/bin/env bash
# v6a (LIVE 92.81): v3 backbone + cascade of high-precision passes on one shared open-item ledger.
#   ./run_v4.sh live | final      -> sub_<phase>_v4.csv.gz  (validated)
# Setup once: python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r requirements-v35.txt
# Optional: V3_BACKBONE=<csv> to use another backbone output instead of running solution/bankrec.py.
set -euo pipefail
PHASE=${1:?usage: ./run_v4.sh live|final}
cd "$(dirname "$0")"
PY=${PY:-.venv/bin/python}
case $PHASE in live) CHAIN="train live";; final) CHAIN="train live final";; *) echo "phase must be live|final"; exit 1;; esac
if [ -z "${V3_BACKBONE:-}" ]; then
  echo "== 1/4 v3 backbone (solution/bankrec.py)"; python3 solution/bankrec.py --phase $PHASE --out sub_${PHASE}_v3.csv.gz
  V3_BACKBONE=sub_${PHASE}_v3.csv.gz
fi
echo "== 2/4 remittance advices + PDF clauses ($CHAIN; cached phases are skipped)"
for p in $CHAIN; do $PY src/docs_extract.py --phase $p; done
echo "== 3/4 v4 cascade"
EXC_R5=1 PAYER_DIR=1 V3_SUB=$V3_BACKBONE $PY src/pipeline.py --phase $PHASE \
  --passes batches,remittance,ap_match,nonitem,ar_multi,ar_pdf,v3import,ar_match,ar_multi_late,v3rest,exc_fix,cancel_guard \
  --out sub_${PHASE}_v6a.csv.gz --sources sub_${PHASE}_v6a_src.csv
echo "== 4/4 validate"
$PY src/validate_submission.py sub_${PHASE}_v6a.csv.gz --phase $PHASE
