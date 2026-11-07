"""Score a --phase dev submission with the official scorer, restricted to the held-out TRAIN months.

    python solution/bankrec.py --phase dev --out sub_dev.csv.gz --why why_dev.csv
    python solution/tools/score_dev.py sub_dev.csv.gz [--months 202504,202505] [--segments 40]

Writes the filtered golden to solution/cache/ so diagnose.py / rowloss.py can use it (--phase dev).
"""
import argparse
import csv
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "starter"))
import bankrec_score as S  # noqa: E402

EXC = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
       "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]


def dev_files(months):
    """(bank_transactions, golden) of the window, cached under solution/cache/dev_<months>/"""
    d = os.path.join(ROOT, "solution", "cache", "dev_" + "_".join(months))
    bt, gd = os.path.join(d, "bank_transactions.csv.gz"), os.path.join(d, "golden_allocations.csv.gz")
    if os.path.exists(gd):
        return bt, gd
    os.makedirs(d, exist_ok=True)
    ids = set()
    with gzip.open(os.path.join(ROOT, "train", "bank_transactions.csv.gz"), "rt", encoding="utf-8") as fh, \
            gzip.open(bt, "wt", encoding="utf-8", newline="") as out:
        rd = csv.DictReader(fh)
        w = csv.DictWriter(out, rd.fieldnames, lineterminator="\n")
        w.writeheader()
        for r in rd:
            if r["booking_date"][:7].replace("-", "") in months:
                ids.add(r["bank_txn_id"])
                w.writerow(r)
    with gzip.open(os.path.join(ROOT, "train", "golden_allocations.csv.gz"), "rt", encoding="utf-8") as fh, \
            gzip.open(gd, "wt", encoding="utf-8", newline="") as out:
        rd = csv.DictReader(fh)
        w = csv.DictWriter(out, rd.fieldnames, lineterminator="\n")
        w.writeheader()
        for r in rd:
            if r["bank_txn_id"] in ids:
                w.writerow(r)
    return bt, gd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("submission")
    ap.add_argument("--months", default="202504,202505")
    ap.add_argument("--segments", type=int, default=40)
    a = ap.parse_args()
    months = sorted(a.months.split(","))
    bt, gd = dev_files(months)
    cats = [r["category"] for r in csv.DictReader(open(os.path.join(ROOT, "masters", "categories.csv"), encoding="utf-8"))]
    g = S.load_golden(gd, bt, EXC, cats)
    res = S.score_submission(g, open(a.submission, "rb").read(), a.submission)
    print(json.dumps({k: res[k] for k in ("ok", "score", "metrics", "errors")}, indent=1))
    for k, v in sorted(res["segments"].items(), key=lambda x: -x[1]["n"])[:a.segments]:
        print(f"  {k:32s} n={v['n']:7d} acc={v['accuracy']:.3f}")


if __name__ == "__main__":
    main()
