#!/usr/bin/env bash
# End-to-end reconciliation run: rule backbone -> document extraction -> pass cascade -> best-guess postfix -> validation.
# This is the configuration of the submitted FINAL file (v8d: FINAL 95.06, LIVE 94.67).
#
#   scripts/run.sh live        # ~25 min, peak ~7 GB RAM; writes out/sub_live.csv.gz
#   scripts/run.sh final       # same on the FINAL phase (needs data/final/)
#   scripts/run.sh sample      # one labelled month, ~25 min: smoke test (postfix skipped)
#
# Env: PY (python, default .venv), BANKREC_DATA (default ./data), BANKREC_CACHE (default ./cache/cascade), OUT (default ./out)
set -euo pipefail
PHASE=${1:?usage: scripts/run.sh live|final|sample}
case $PHASE in
  sample) CHAIN="sample" ;;
  live)   CHAIN="train live" ;;
  final)  CHAIN="train live final" ;;
  *) echo "phase must be live, final or sample"; exit 1 ;;
esac

cd "$(dirname "$0")/.."
if [ -z "${PY:-}" ]; then
  if [ -x .venv/bin/python ]; then PY=.venv/bin/python; elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe; else PY=python3; fi
fi
DATA=${BANKREC_DATA:-data}
OUT=${OUT:-out}
mkdir -p "$OUT"
[ -f "$DATA/$PHASE/bank_transactions.csv.gz" ] || { echo "missing $DATA/$PHASE/bank_transactions.csv.gz (see README: Quickstart)"; exit 1; }
command -v pdftotext >/dev/null || echo "warning: pdftotext not on PATH; PDF remittance advices will be skipped by the backbone"

# FINAL may ship a partial masters_final/: complete it with the base masters, file by file (never overwrites)
if [ "$PHASE" = final ] && [ -d "$DATA/final/masters_final" ]; then
  for f in "$DATA"/masters/*.csv; do cp -n "$f" "$DATA/final/masters_final/" 2>/dev/null || true; done
fi

PASSES=batches,remittance,ap_match,nonitem,ar_multi,ar_pdf,v3import_strong,ar_match,ar_multi_late,ar_bigpool,ar_dispute,ar_guess,v3import,v3rest,exc_fix,cancel_guard
export AR_DUST=1 EXC_R5=1 PAYER_DIR=1 AR_GUESS=1

t() { date +%H:%M:%S; }
echo "== 1/5 rule backbone ($(t))"
$PY backbone/bankrec.py --phase "$PHASE" --data "$DATA" --out "$OUT/sub_${PHASE}_backbone.csv.gz"

echo "== 2/5 remittance advices + PDF clauses ($(t))"
for p in $CHAIN; do $PY src/docs_extract.py --phase "$p" --data "$DATA"; done

echo "== 3/5 pass cascade ($(t))"
V3_SUB="$OUT/sub_${PHASE}_backbone.csv.gz" $PY src/pipeline.py --phase "$PHASE" --data "$DATA" --passes "$PASSES" \
  --out "$OUT/sub_${PHASE}_cascade.csv.gz" --sources "$OUT/${PHASE}_sources.csv"

if [ "$PHASE" = sample ]; then
  cp "$OUT/sub_${PHASE}_cascade.csv.gz" "$OUT/sub_${PHASE}.csv.gz"
  echo "== 4/5 postfix skipped on sample"
else
  echo "== 4/5 best-guess postfix, 130-day window ($(t))"
  $PY src/postfix.py "$OUT/sub_${PHASE}_cascade.csv.gz" --phase "$PHASE" --win 130 --out "$OUT/sub_${PHASE}.csv.gz"
fi

echo "== 5/5 validate ($(t))"
$PY src/validate_submission.py "$OUT/sub_${PHASE}.csv.gz" --phase "$PHASE" --data "$DATA"
echo "done ($(t)): $OUT/sub_${PHASE}.csv.gz"
