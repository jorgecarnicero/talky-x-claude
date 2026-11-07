"""Run the reconciliation pipeline and write a submission.

    .venv/bin/python src/pipeline.py --phase train --from 2024-07-01 --to 2024-12-31 --out ../out/dev.csv.gz
    .venv/bin/python src/pipeline.py --phase live --out ../out/live.csv.gz
    .venv/bin/python src/pipeline.py --phase train --from ... --passes nonitem,batches   # subset of passes

Each pass lives in src/passes/<name>.py and exposes run(ctx). Passes run in PASS_ORDER; a bank line resolved by an
earlier pass is never touched again. Unresolved lines get the fallback (receipt -> UNIDENTIFIED, payment -> BANK_FEES).
"""
import argparse
import csv
import gzip
import importlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import DEFAULT_DATA, Ctx  # noqa: E402

PASS_ORDER = ["batches", "remittance", "ap_match", "nonitem", "ar_multi", "ar_pdf", "v3import", "ar_match", "ar_multi_late", "v3rest", "exc_fix", "cancel_guard"]


def fallback(ctx):
    for t in ctx.pending().itertuples():
        if t.amount > 0:
            ctx.allocate(t.bank_txn_id, [{"category": "UNIDENTIFIED", "exception": "UNIDENTIFIED"}], "fallback")
        else:
            ctx.allocate(t.bank_txn_id, [{"category": "BANK_FEES"}], "fallback")


def write(ctx, out):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    op = gzip.open if out.endswith(".gz") else open
    n = 0
    with op(out, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["bank_txn_id", "item_id", "amount", "category", "exception"])
        for t in ctx.bank.bank_txn_id:
            seen = set()
            for r in ctx.results[t]:
                if r["item_id"]:
                    if r["item_id"] in seen:
                        continue
                    seen.add(r["item_id"])
                    w.writerow([t, r["item_id"], f"{abs(r['amount']):.2f}", "", r["exception"]])
                else:
                    w.writerow([t, "", "", r["category"], r["exception"]])
                    break
                n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--phase", default="train", choices=["sample", "train", "live", "final"])
    ap.add_argument("--from", dest="dfrom")
    ap.add_argument("--to", dest="dto")
    ap.add_argument("--passes", default=",".join(PASS_ORDER))
    ap.add_argument("--out", required=True)
    ap.add_argument("--sources", help="optional csv: bank_txn_id,source pass (diagnostics)")
    a = ap.parse_args()
    t0 = time.time()
    ctx = Ctx(a.data, a.phase, a.dfrom, a.dto)
    print(f"loaded {len(ctx.bank)} bank lines, {len(ctx.items)} items, {len(ctx.open)} open  [{time.time()-t0:.0f}s]", flush=True)
    for name in [p for p in a.passes.split(",") if p]:
        try:
            mod = importlib.import_module(f"passes.{name}")
        except ModuleNotFoundError as e:
            print(f"  pass {name}: not available ({e})")
            continue
        before, t1 = len(ctx.results), time.time()
        mod.run(ctx)
        print(f"  pass {name:12s} resolved {len(ctx.results)-before:7d}  [{time.time()-t1:.0f}s]", flush=True)
    before = len(ctx.results)
    fallback(ctx)
    print(f"  fallback        {len(ctx.results)-before:7d}")
    n = write(ctx, a.out)
    if a.sources:
        with open(a.sources, "w") as fh:
            fh.write("bank_txn_id,source\n" + "".join(f"{t},{s}\n" for t, s in ctx.source.items()))
    print(f"wrote {a.out}  [{time.time()-t0:.0f}s total]")


if __name__ == "__main__":
    main()
