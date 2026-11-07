#!/usr/bin/env bash
# FINAL with v8c-pf = v8b-pf + ZA01 EFT CREDIT payer pattern. Reuses out/sub_final_v3.csv.gz and the FINAL docs caches made by RUN_FINAL.sh.
set -euo pipefail
P=~/Downloads/hackathons/claude-talky-accounting; cd "$P"
export AR_DUST=1 EXC_R5=1 PAYER_DIR=1 AR_GUESS=1 BANKREC_CACHE="$P/cache"
PY="$P/repo/.venv/bin/python"
PASSES=batches,remittance,ap_match,nonitem,ar_multi,ar_pdf,v3import_strong,ar_match,ar_multi_late,ar_bigpool,ar_dispute,ar_guess,v3import,v3rest,exc_fix,cancel_guard
[ -f out/sub_final_v3.csv.gz ] || { echo "missing out/sub_final_v3.csv.gz (run RUN_FINAL.sh first)"; exit 1; }
echo "== docs (cached phases skipped) $(date +%H:%M)"
ls cache/docs_ra_final.parquet cache/docs_pdf_final.parquet || { echo "FINAL docs caches missing"; exit 1; }
echo "== v8b pipeline $(date +%H:%M)"
(cd FINAL_CODE_v8c && V3_SUB=../out/sub_final_v3.csv.gz $PY src/pipeline.py --phase final --passes $PASSES \
   --out ../out/sub_final_v8c.csv.gz --sources ../out/final_v8c_src.csv) > out/run_final_v8c.log 2>&1
grep -E "pass |wrote" out/run_final_v8c.log
echo "== postfix 100d / 130d $(date +%H:%M)"
(cd FINAL_CODE_v7c && $PY postfix.py ../out/sub_final_v8c.csv.gz --phase final --out ../out/sub_final_v8c_pf.csv.gz)
(cd FINAL_CODE_v7c && $PY postfix.py ../out/sub_final_v8c.csv.gz --phase final --win 130 --out ../out/sub_final_v8c_pf130.csv.gz)
echo "== validate $(date +%H:%M)"
for f in sub_final_v8c_pf sub_final_v8c_pf130; do (cd FINAL_CODE_v8c && $PY src/validate_submission.py ../out/$f.csv.gz --phase final); done
echo "DONE $(date +%H:%M)"
