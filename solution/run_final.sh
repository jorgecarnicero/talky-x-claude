#!/usr/bin/env bash
# Official command for LIVE / FINAL (same chain and env as dev.py and test_branch.sh, all read solution/cascade.conf):
#   solution/run_final.sh live | final      -> sub_<phase>_final.csv.gz (validated)
# Env: PY (python, default .venv/bin/python), PYTHONHASHSEED is pinned to 0, BANKREC_DATA/BANKREC_CACHE optional.
set -euo pipefail
PHASE=${1:?usage: solution/run_final.sh live|final}
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
export PYTHONHASHSEED=0
set -a; . <(grep -E '^[A-Z_0-9]+=' solution/cascade.conf); set +a   # PASSES, EXC_R5, PAYER_DIR
case $PHASE in live) CHAIN="train live";; final) CHAIN="train live final";; *) echo "phase must be live|final"; exit 1;; esac
DATA_ARG=(); [ -n "${BANKREC_DATA:-}" ] && DATA_ARG=(--data "$BANKREC_DATA")
if [ -z "${V3_BACKBONE:-}" ]; then
  echo "== 1/4 v3 backbone"; python3 solution/bankrec.py --phase $PHASE ${DATA_ARG[@]+"${DATA_ARG[@]}"} --out sub_${PHASE}_v3.csv.gz
  V3_BACKBONE=sub_${PHASE}_v3.csv.gz
fi
echo "== 2/4 remittance advices + PDF clauses ($CHAIN)"
for p in $CHAIN; do $PY src/docs_extract.py ${DATA_ARG[@]+"${DATA_ARG[@]}"} --phase $p; done
echo "== 3/4 cascade: $PASSES"
V3_SUB=$V3_BACKBONE $PY src/pipeline.py ${DATA_ARG[@]+"${DATA_ARG[@]}"} --phase $PHASE --passes "$PASSES" \
  --out sub_${PHASE}_final.csv.gz --sources sub_${PHASE}_final_src.csv
echo "== 4/4 validate"
$PY src/validate_submission.py sub_${PHASE}_final.csv.gz --phase $PHASE
