"""Build the dataset folder that the team's solution/tools/significance.py (gates G1-G3) expects, for one TRAIN window:

    python src/sigdata.py --from 2025-04-01 --to 2025-05-31 --out ../out/dev/sig_dev

<out>/golden_dev.csv                     golden rows of the bank lines booked in the window
<out>/live/bank_transactions.csv.gz      those bank lines
<out>/live/open_items_at_start.csv.gz    item_id, party_id of the golden items (only used to group lines by payer)
Read-only on the data; measurement only, never an input to a rule.
"""
import argparse
import csv
import gzip
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import CACHE, DEFAULT_DATA  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--from", dest="dfrom", required=True)
ap.add_argument("--to", dest="dto", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--data", default=DEFAULT_DATA)
a = ap.parse_args()
os.makedirs(os.path.join(a.out, "live"), exist_ok=True)
keep = set()
src = os.path.join(a.data, "train", "bank_transactions.csv.gz")
with gzip.open(src, "rt", encoding="utf-8") as fh, \
        gzip.open(os.path.join(a.out, "live", "bank_transactions.csv.gz"), "wt", encoding="utf-8", newline="") as out:
    rd = csv.DictReader(fh)
    w = csv.DictWriter(out, rd.fieldnames, lineterminator="\n")
    w.writeheader()
    for r in rd:
        if a.dfrom <= r["booking_date"] <= a.dto:
            keep.add(r["bank_txn_id"])
            w.writerow(r)
items = set()
with gzip.open(os.path.join(a.data, "train", "golden_allocations.csv.gz"), "rt", encoding="utf-8") as fh, \
        open(os.path.join(a.out, "golden_dev.csv"), "w", encoding="utf-8", newline="") as out:
    rd = csv.DictReader(fh)
    w = csv.DictWriter(out, rd.fieldnames, lineterminator="\n")
    w.writeheader()
    for r in rd:
        if r["bank_txn_id"] in keep:
            w.writerow(r)
            if r["item_id"]:
                items.add(r["item_id"])
it = pd.read_parquet(os.path.join(CACHE, "items_train.parquet"), columns=["item_id", "party_id"])
it[it.item_id.isin(items)].to_csv(os.path.join(a.out, "live", "open_items_at_start.csv.gz"), index=False)
print(f"{a.out}: {len(keep)} bank lines, {len(items)} golden items")
