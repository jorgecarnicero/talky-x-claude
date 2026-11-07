#!/usr/bin/env bash
# Reproduce a v3.5 submission:  ./run_v35.sh live   |   ./run_v35.sh final
# Needs: team v3 code checked out at ../team-main (model/v3 = main@fa9ed69) with data symlinked there,
#        our data layout in ../dataroot, and .venv (see README).
set -euo pipefail
PHASE=${1:?phase live|final}
cd "$(dirname "$0")"
OUT=../out; mkdir -p $OUT
echo "== 1/4 v3 (team code)"; (cd ../team-main && python3 solution/bankrec.py --phase $PHASE --out ../out/sub_${PHASE}_v3.csv.gz)
echo "== 2/4 docs extraction"; .venv/bin/python src/docs_extract.py --phase $PHASE
echo "== 3/4 v3.5 pipeline"; V3_SUB=$OUT/sub_${PHASE}_v3.csv.gz .venv/bin/python src/pipeline.py --phase $PHASE \
  --passes batches,remittance,ap_match,nonitem,v3import,ar_match,v3rest --out $OUT/sub_${PHASE}_v35.csv.gz --sources $OUT/${PHASE}_v35_src.csv
echo "== 4/4 validate"; .venv/bin/python src/validate_submission.py $OUT/sub_${PHASE}_v35.csv.gz --phase $PHASE
