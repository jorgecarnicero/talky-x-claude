#!/usr/bin/env bash
# A/B runner for LIVE / FINAL from a clean checkout of a given commit.
#   A = trunk exactly as solution/cascade.conf says (EXC_OVERPAY=1)
#   B = same commit with EXC_OVERPAY=0 (the v6c chain)
#
# Usage:
#   solution/tools/final_ab.sh <hash> <data_dir> <out_dir> [live|final] [--dry-run]
# Env:
#   PY        python with the project deps (default: .venv/bin/python of this repo)
#   PEAK_GB   peak RSS of one run, used for the parallel/sequential decision (default 9; measured 8.3 GB on LIVE, 2a8fd84)
#   FORCE_SEQ=1 always run A then B
#
# Output: <out>/A/sub.csv.gz, <out>/B/sub.csv.gz, <out>/{A,B}/run.log, <out>/{A,B}/validate.txt, <out>/times.txt,
#         <out>/summary.txt (rows, unanswered lines, duplicate pairs, UNIDENTIFIED % per entity, A-vs-B differences).
#
# Notes:
#  - B is configured by editing EXC_OVERPAY in B's own checkout of cascade.conf: run_final.sh evals cascade.conf AFTER the
#    environment, so `EXC_OVERPAY=0 solution/run_final.sh` would silently run A.
#  - run_final.sh expands $PY unquoted: a PY path with spaces is replaced by a symlink under <out>.
#  - Each run gets its own checkout and its own BANKREC_CACHE (no shared writes when running in parallel).
set -euo pipefail

usage() { sed -n '6,7p' "$0"; exit 2; }
DRY=0; ARGS=()
for x in "$@"; do [ "$x" = "--dry-run" ] && DRY=1 || ARGS+=("$x"); done
[ ${#ARGS[@]} -ge 3 ] || usage
HASH=${ARGS[0]}; DATA=${ARGS[1]}; OUT=${ARGS[2]}; PHASE=${ARGS[3]:-live}
case $PHASE in live|final) ;; *) usage;; esac

REPO=$(cd "$(dirname "$0")/../.." && pwd)
PY=${PY:-$REPO/.venv/bin/python}
PEAK_GB=${PEAK_GB:-9}

say() { echo "[$(date '+%T')] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

# ---------- preflight (also done in --dry-run) ----------
case "$OUT" in *" "*) die "out_dir must not contain spaces (run_final.sh does not quote paths): $OUT";; esac
DATA=$(cd "$DATA" 2>/dev/null && pwd) || die "data dir not found: ${ARGS[1]}"
[ -f "$DATA/$PHASE/bank_transactions.csv.gz" ] || die "missing $DATA/$PHASE/bank_transactions.csv.gz"
[ -d "$DATA/masters" ] || die "missing $DATA/masters"
[ -x "$PY" ] || die "python not found: $PY (set PY=...)"
"$PY" -c "import pandas" 2>/dev/null || die "pandas not importable with $PY"
command -v pdftotext >/dev/null || die "pdftotext not on PATH (live/final abort without it)"
FULL=$(git -C "$REPO" rev-parse --verify "$HASH^{commit}") || die "unknown commit $HASH"
git -C "$REPO" show "$FULL:solution/cascade.conf" | grep -q '^EXC_OVERPAY=1$' || die "$HASH: cascade.conf has no EXC_OVERPAY=1 line"
git -C "$REPO" show "$FULL:solution/run_final.sh" >/dev/null || die "$HASH: no solution/run_final.sh"
[ -e "$OUT/A" ] || [ -e "$OUT/B" ] && die "$OUT/A or $OUT/B already exists (pick a new out_dir)"
OTHER=$(pgrep -fl 'src/pipeline.py|solution/bankrec.py|docs_extract.py|tools/dev.py' || true)
[ -n "$OTHER" ] && echo "WARNING: another pipeline is running:" && echo "$OTHER"

# free memory = free + inactive + speculative + purgeable pages
PAGE=$(sysctl -n hw.pagesize)
FREE_PAGES=$(vm_stat | awk -F: '/Pages (free|inactive|speculative|purgeable)/ {gsub(/[ .]/,"",$2); s+=$2} END {print s}')
FREE_GB=$(( FREE_PAGES * PAGE / 1024 / 1024 / 1024 ))
TOTAL_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
NEED_GB=$(( 2 * PEAK_GB + 2 ))
if [ "${FORCE_SEQ:-0}" = "1" ] || [ -n "$OTHER" ] || [ "$FREE_GB" -lt "$NEED_GB" ]; then
  MODE=seq
  say "memory: ~${FREE_GB} GB available of ${TOTAL_GB} GB; two runs need ~${NEED_GB} GB -> WARNING: they do not fit together, running A then B"
else
  MODE=par
  say "memory: ~${FREE_GB} GB available of ${TOTAL_GB} GB; two runs need ~${NEED_GB} GB -> running A and B in parallel"
fi

say "commit $FULL  phase $PHASE  data $DATA  out $OUT  mode $MODE"
if [ $DRY = 1 ]; then
  echo "DRY RUN: would create clean checkouts $OUT/src_A, $OUT/src_B at ${FULL:0:7}; B gets EXC_OVERPAY=0 in its cascade.conf"
  echo "DRY RUN: would run  PYTHONHASHSEED=0 BANKREC_DATA=$DATA BANKREC_CACHE=$OUT/<X>/cache solution/run_final.sh $PHASE  ($MODE)"
  echo "DRY RUN: then validate.py on both, summary table to $OUT/summary.txt. Nothing was launched."
  exit 0
fi

# ---------- setup ----------
mkdir -p "$OUT/A" "$OUT/B"
OUT=$(cd "$OUT" && pwd)
case "$PY" in *" "*) ln -sfn "$(dirname "$(dirname "$PY")")" "$OUT/venv"; PY="$OUT/venv/bin/python";; esac
for X in A B; do
  git -C "$REPO" worktree add --detach "$OUT/src_$X" "$FULL" >/dev/null
  [ -z "$(git -C "$OUT/src_$X" status --porcelain)" ] || die "checkout $X not clean"
done
sed -i.bak 's/^EXC_OVERPAY=1$/EXC_OVERPAY=0/' "$OUT/src_B/solution/cascade.conf" && rm "$OUT/src_B/solution/cascade.conf.bak"
grep -q '^EXC_OVERPAY=0$' "$OUT/src_B/solution/cascade.conf" || die "could not set EXC_OVERPAY=0 for B"

run_one() {  # $1 = A|B
  local X=$1 rc t0 t1
  t0=$(date '+%F %T'); echo "START $X $t0" >> "$OUT/times.txt"; say "START $X"
  ( cd "$OUT/src_$X" && env PY="$PY" PYTHONHASHSEED=0 BANKREC_DATA="$DATA" BANKREC_CACHE="$OUT/$X/cache" \
      /usr/bin/time -l bash solution/run_final.sh "$PHASE" ) > "$OUT/$X/run.log" 2>&1 && rc=0 || rc=$?
  t1=$(date '+%F %T')
  local rss; rss=$(awk '/maximum resident/ {printf "%.1f GB", $1/1073741824}' "$OUT/$X/run.log")
  echo "END   $X $t1 rc=$rc peak=$rss" >> "$OUT/times.txt"; say "END $X rc=$rc peak=$rss"
  [ $rc = 0 ] && cp "$OUT/src_$X/sub_${PHASE}_final.csv.gz" "$OUT/$X/sub.csv.gz"
  return $rc
}

RCA=0; RCB=0
if [ $MODE = par ]; then
  run_one A & PA=$!; run_one B & PB=$!
  wait $PA || RCA=$?; wait $PB || RCB=$?
else
  run_one A || RCA=$?
  run_one B || RCB=$?
fi

# ---------- validate ----------
for X in A B; do
  if [ -f "$OUT/$X/sub.csv.gz" ]; then
    ( cd "$DATA" && "$PY" "$OUT/src_$X/solution/tools/validate.py" "$OUT/$X/sub.csv.gz" --phase "$PHASE" ) > "$OUT/$X/validate.txt" 2>&1 || true
    say "validate $X: $(tr '\n' ' ' < "$OUT/$X/validate.txt")"
  else
    say "validate $X: no submission (run failed, see $OUT/$X/run.log)"
  fi
done

# ---------- summary ----------
"$PY" - "$DATA" "$PHASE" "$OUT" <<'PYEOF' | tee "$OUT/summary.txt"
import os, sys
import pandas as pd
data, phase, out = sys.argv[1:4]
b = pd.read_csv(os.path.join(data, phase, "bank_transactions.csv.gz"), usecols=["bank_txn_id", "entity", "amount"])
subs = {}
for x in "AB":
    p = os.path.join(out, x, "sub.csv.gz")
    if os.path.exists(p):
        subs[x] = pd.read_csv(p, dtype=str, keep_default_na=False)
rows = []
def cell(x, f):
    return f(subs[x]) if x in subs else "–"
print(f"\n{'':34s}{'A (EXC_OVERPAY=1)':>20s}{'B (EXC_OVERPAY=0)':>20s}")
def line(label, f):
    print(f"{label:34s}{str(cell('A', f)):>20s}{str(cell('B', f)):>20s}")
line("rows", len)
line("bank lines without an answer", lambda s: len(set(b.bank_txn_id) - set(s.bank_txn_id)))
line("duplicate (bank_txn_id, item_id)", lambda s: int(s.duplicated(["bank_txn_id", "item_id"]).sum()))
line("UNIDENTIFIED lines (all)", lambda s: s.loc[s.exception == "UNIDENTIFIED", "bank_txn_id"].nunique())
print("\nUNIDENTIFIED % per entity (receipts only)")
rec = b[b.amount > 0]
for e, g in rec.groupby("entity"):
    vals = []
    for x in "AB":
        if x in subs:
            u = set(subs[x].loc[subs[x].exception == "UNIDENTIFIED", "bank_txn_id"])
            vals.append(f"{g.bank_txn_id.isin(u).mean()*100:5.1f} %")
        else:
            vals.append("–")
    print(f"   {e:31s}{vals[0]:>20s}{vals[1]:>20s}")
if len(subs) == 2:
    key = ["bank_txn_id", "item_id", "amount", "category", "exception"]
    norm = lambda s: s[key].assign(amount=lambda d: d.amount.map(lambda v: f"{float(v):.2f}" if v else ""))
    a, bb = norm(subs["A"]), norm(subs["B"])
    m = a.merge(bb, how="outer", on=key, indicator=True)
    only_a, only_b = int((m._merge == "left_only").sum()), int((m._merge == "right_only").sum())
    lines = set(m.loc[m._merge != "both", "bank_txn_id"])
    print(f"\nA vs B: {only_a} rows only in A, {only_b} rows only in B; {len(lines)} bank lines answered differently")
PYEOF
cat "$OUT/times.txt"
say "outputs: $OUT/A/sub.csv.gz  $OUT/B/sub.csv.gz  (checkouts $OUT/src_A, $OUT/src_B: remove with git worktree remove)"
[ $RCA = 0 ] && [ $RCB = 0 ]
