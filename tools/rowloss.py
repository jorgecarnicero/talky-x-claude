"""Which segments lose the most allocation rows (allocation F1 = 50 % of the score)?

    python tools/rowloss.py sub_sample.csv.gz [--phase sample] [--top 20]
"""
import argparse
import csv
import gzip
from collections import Counter
import os

DATA = os.environ.get("BANKREC_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("submission")
    ap.add_argument("--phase", default="sample", choices=["sample", "train", "dev"])
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    sub = {}
    for r in csv.DictReader(gzip.open(a.submission, "rt", encoding="utf-8")):
        if r["item_id"]:
            sub[(r["bank_txn_id"], r["item_id"])] = float(r["amount"])
    tot, tp, gold = Counter(), Counter(), set()
    for r in csv.DictReader(gzip.open((os.path.join(DATA, "_cache", "dev_202504_202505")+"" if a.phase == "dev" else os.path.join(DATA, a.phase)) + "/golden_allocations.csv.gz", "rt", encoding="utf-8")):
        if not r["item_id"]:
            continue
        k = (r["bank_txn_id"], r["item_id"])
        gold.add(k)
        g = float(r["amount"])
        tot[r["segment"]] += 1
        if k in sub and abs(sub[k] - g) <= max(0.01, 0.005 * g):
            tp[r["segment"]] += 1
    n = sum(tot.values())
    print(f"golden item rows {n}, submitted item rows {len(sub)}, false-positive pairs {len(set(sub) - gold)}")
    for s, c in sorted(tot.items(), key=lambda x: -(x[1] - tp[x[0]]))[:a.top]:
        print(f"{s:30s} rows={c:7d} hit={tp[s]:7d} missed={c - tp[s]:7d} ({(c - tp[s]) / n * 100:4.1f}% of all)")


if __name__ == "__main__":
    main()
