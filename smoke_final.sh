#!/usr/bin/env bash
# Robustness smoke test (docs/PROTOCOL.md step 3): synthetic FINAL with a new entity (QA01) + new currency (QAR, no FX
# rate), partial masters_final. Uses its OWN cache dir so no synthetic final cache can ever leak into the real FINAL.
set -euo pipefail
cd "$(dirname "$0")"; ROOT=$(cd .. && pwd)
if [ -z "${PY:-}" ]; then [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=.venv/Scripts/python.exe; fi
S=$ROOT/out/synth; C=$ROOT/out/synth_cache; mkdir -p "$C"
for f in "$ROOT"/cache-v35/{docs_ra_train,docs_pdf_train,docs_ra_live,docs_pdf_live,bank_train,items_train,golden_train,bank_live,items_live}.parquet; do
  [ -e "$C/$(basename "$f")" ] || cp "$f" "$C/"; done
BANKREC_DATA=$ROOT/talky-x-claude $PY src/synth_final.py --out "$S"
export BANKREC_DATA=$S BANKREC_CACHE=$C PYTHONWARNINGS=ignore
python3 solution/bankrec.py --data "$S" --phase final --out "$S/bb_final.csv.gz" > "$S/bb_final.log" 2>&1 || { echo "BACKBONE FAILED"; tail -5 "$S/bb_final.log"; exit 1; }
$PY src/docs_extract.py --phase final > "$S/docs.log" 2>&1 || { echo "DOCS FAILED"; tail -5 "$S/docs.log"; exit 1; }
AR_DUST=1 EXC_R5=1 PAYER_DIR=1 V3_SUB="$S/bb_final.csv.gz" $PY src/pipeline.py --phase final \
  --out "$S/sub_final.csv.gz" > "$S/pipe.log" 2>&1 || { echo "PIPELINE FAILED"; tail -8 "$S/pipe.log"; exit 1; }
$PY src/validate_submission.py "$S/sub_final.csv.gz" --phase final --data "$S" | tail -3
grep -E "Trace|Error" "$S"/*.log && echo "ERRORS IN LOGS" || echo "SMOKE OK: no errors"
