#!/usr/bin/env bash
# ONE-COMMAND FINAL SUBMISSION (v6c code, frozen copy in FINAL_CODE_v6c/). Works without Claude.
#
#   1. Download the FINAL archive from the hackathon site (Datos page) into:
#        ~/Downloads/hackathons/claude-talky-accounting/data/
#   2. Run:   bash RUN_FINAL.sh (lead machine layout) <path-to-final-archive.tar.gz or extracted folder>
#   3. Upload the file it prints at the end (out/sub_final_v6c.csv.gz) ONCE on the Enviar page (FINAL).
#
# Takes ~20-25 min. Close other heavy apps first. Never run two copies at once.
# Also works for a dry run on LIVE:   bash RUN_FINAL.sh --live
set -euo pipefail
P=~/Downloads/hackathons/claude-talky-accounting
cd "$P"
PASSES=batches,remittance,ap_match,nonitem,ar_multi,ar_pdf,v3import,ar_match,ar_multi_late,ar_bigpool,v3rest,exc_fix,cancel_guard
export AR_DUST=1 EXC_R5=1 PAYER_DIR=1 BANKREC_CACHE="$P/cache"
PY="$P/repo/.venv/bin/python"

if [ "${1:-}" = "--live" ]; then
  PHASE=live
else
  PHASE=final
  SRC=${1:?usage: RUN_FINAL.sh <final archive .tar.gz | extracted folder> | --live}
  echo "== 1/6 unpack + link FINAL data  ($(date +%H:%M))"
  mkdir -p data/final
  case "$SRC" in
    *.zip) unzip -oq "$SRC" -d data/final ;;
    *.tar.gz|*.tgz) tar -xzf "$SRC" -C data/final ;;
  esac
  if [ -d "$SRC" ] && [ "$(cd "$SRC" && pwd)" != "$P/data/final" ]; then cp -R "$SRC"/. data/final/; fi
  # find the folder that holds bank_transactions.csv.gz
  FDIR=$(dirname "$(find "$P/data/final" -name bank_transactions.csv.gz | head -1)")
  [ -f "$FDIR/bank_transactions.csv.gz" ] || { echo "ERROR: no bank_transactions.csv.gz found in FINAL data"; exit 1; }
  echo "   FINAL folder: $FDIR"
  # dataroot/final = real dir of symlinks; masters_final = all current masters overlaid by FINAL's (complete either way)
  rm -rf dataroot/final && mkdir -p dataroot/final/masters_final
  for e in "$FDIR"/*; do b=$(basename "$e"); [ "$b" = masters_final ] || ln -s "$e" "dataroot/final/$b"; done
  for f in dataroot/masters/*.csv; do ln -s "$P/$f" "dataroot/final/masters_final/$(basename "$f")"; done
  MF=$(find "$P/data/final" -type d -name masters_final | head -1)
  if [ -n "$MF" ]; then for f in "$MF"/*.csv; do ln -sf "$f" "dataroot/final/masters_final/$(basename "$f")"; done; fi
  ls dataroot/final/masters_final
  ln -sfn ../dataroot/final team-main/final
  echo "== 2/6 clear stale FINAL caches"
  rm -f cache/*final* team-main/solution/cache/*final* team-main/solution/cache/v35/*final* 2>/dev/null || true
fi

echo "== 3/6 v3 backbone (team code)  ($(date +%H:%M))"
(cd team-main && python3 solution/bankrec.py --phase $PHASE --out ../out/sub_${PHASE}_v3.csv.gz) > out/run_${PHASE}_v3.log 2>&1 \
  || { echo "v3 FAILED - see out/run_${PHASE}_v3.log"; exit 1; }
tail -1 out/run_${PHASE}_v3.log

echo "== 4/6 remittance advices + PDF clauses  ($(date +%H:%M))"
for ph in train live $PHASE; do (cd FINAL_CODE_v6c && $PY src/docs_extract.py --phase $ph) >> out/run_${PHASE}_docs.log 2>&1; done

echo "== 5/6 v6c pipeline  ($(date +%H:%M))"
(cd FINAL_CODE_v6c && V3_SUB=../out/sub_${PHASE}_v3.csv.gz $PY src/pipeline.py --phase $PHASE --passes $PASSES \
  --out ../out/sub_${PHASE}_v6c.csv.gz --sources ../out/${PHASE}_v6c_src.csv) > out/run_${PHASE}_v6c.log 2>&1 \
  || { echo "PIPELINE FAILED - see out/run_${PHASE}_v6c.log. FALLBACK: upload out/sub_${PHASE}_v3.csv.gz (validate it first)"; exit 1; }
grep -E "pass |wrote" out/run_${PHASE}_v6c.log

echo "== 6/6 validate  ($(date +%H:%M))"
(cd FINAL_CODE_v6c && $PY src/validate_submission.py ../out/sub_${PHASE}_v6c.csv.gz --phase $PHASE)
echo
echo ">>> UPLOAD THIS FILE: $P/out/sub_${PHASE}_v6c.csv.gz   (only if it says VALID above)"
