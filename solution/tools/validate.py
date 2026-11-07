"""Check a submission against the FORMAT.md validation rules before uploading.

    python solution/tools/validate.py sub_live_v2.csv.gz --phase live
"""
import argparse
import csv
import gzip
from collections import Counter, defaultdict

EXC = {"", "PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
       "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"}


def rd(p):
    return csv.DictReader(gzip.open(p, "rt", encoding="utf-8") if p.endswith(".gz") else open(p, encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("submission")
    ap.add_argument("--phase", default="live", choices=["sample", "train", "live", "final"])
    a = ap.parse_args()
    cats = {r["category"] for r in rd("masters/categories.csv")}
    ids = {r["bank_txn_id"] for r in rd(f"{a.phase}/bank_transactions.csv.gz")}
    seen, kinds, err = set(), Counter(), Counter()
    per_line = defaultdict(Counter)
    n = 0
    for r in rd(a.submission):
        n += 1
        t = r["bank_txn_id"]
        if t not in ids:
            err["bank_txn_id not in phase"] += 1
        if (t, r["item_id"]) in seen:
            err["duplicated (bank_txn_id, item_id)"] += 1
        seen.add((t, r["item_id"]))
        if r["item_id"]:
            kinds["item rows"] += 1
            per_line[t]["item"] += 1
            if r["category"]:
                err["category on item row"] += 1
            try:
                if float(r["amount"]) < 0:
                    err["negative amount"] += 1
            except ValueError:
                err["missing/bad amount"] += 1
        else:
            kinds[r["category"]] += 1
            per_line[t]["cat"] += 1
            if r["amount"]:
                err["amount on no-item row"] += 1
            if r["category"] not in cats:
                err["unknown category"] += 1
        if r["exception"] not in EXC:
            err["unknown exception"] += 1
    err["line mixes items and category"] = sum(1 for c in per_line.values() if c["item"] and c["cat"])
    err["more than one no-item row"] = sum(1 for c in per_line.values() if c["cat"] > 1)
    missing = len(ids - set(per_line))
    err = {k: v for k, v in err.items() if v}
    print(f"rows={n}  lines covered={len(per_line)}/{len(ids)}  missing={missing}")
    print("errors:", err or "none")
    print("row kinds:", dict(kinds.most_common()))
    print("OK to upload" if not err and not missing else "FIX BEFORE UPLOADING")


if __name__ == "__main__":
    main()
