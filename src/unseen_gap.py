"""Unseen-customer robustness test (docs/PROTOCOL.md, step 3) on TUNE.

    UNSEEN_FRAC=0.25 WINDOWS=tune ./dev_eval.sh unseen25
    python src/unseen_gap.py ../out/dev/base_tune.csv.gz ../out/dev/unseen25_tune.csv.gz --frac 0.25

Customers are split by the same hash as payer_dir.unseen(). A line belongs to the customer of its golden items
(lines without AR items are ignored). Reports line accuracy (official rule) of held-out and seen customers in both
runs. The test passes when held-out customers lose < 1.5 points between the two runs, i.e. the model does not
depend on having memorised those customers' history.
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import CACHE, DEFAULT_DATA  # noqa: E402
from payer_dir import unseen  # noqa: E402
from pointslost import load, tol  # noqa: E402


def ok(g, s):
    gi, gc, ge, _ = g
    si, sc, se = s
    if gi:
        return not (sc or se) and len(si) == len(gi) and all(
            i in gi and abs(a - gi[i][0]) <= tol(gi[i][0]) and e == gi[i][1] for i, (a, e) in si.items())
    return not si and sc == gc and se == ge


ap = argparse.ArgumentParser()
ap.add_argument("base")
ap.add_argument("held")
ap.add_argument("--frac", type=float, default=0.25)
ap.add_argument("--data", default=DEFAULT_DATA)
ap.add_argument("--from", dest="dfrom", default="2024-10-01")
ap.add_argument("--to", dest="dto", default="2024-11-30")
a = ap.parse_args()
a.submission = a.base
gold, sb = load(a)
a.submission = a.held
_, sh = load(a)
it = pd.read_parquet(os.path.join(CACHE, "items_train.parquet"), columns=["item_id", "party_id"])
items = {i for g in gold.values() for i in g[0] if i.startswith("AR")}
party = dict(it[it.item_id.isin(items)].itertuples(index=False))
cnt = {k: [0, 0, 0] for k in ("held-out", "seen")}
for t, g in gold.items():
    ps = {party.get(i) for i in g[0] if i.startswith("AR")} - {None}
    if len(ps) != 1:
        continue
    k = "held-out" if unseen(ps.pop(), a.frac) else "seen"
    c = cnt[k]
    c[0] += 1
    c[1] += ok(g, sb.get(t, [{}, "", ""]))
    c[2] += ok(g, sh.get(t, [{}, "", ""]))
for k, (n, x, y) in cnt.items():
    print(f"{k:9s} lines={n:6d}  acc with history {100 * x / n:6.2f}  without {100 * y / n:6.2f}  "
          f"drop {100 * (x - y) / n:+.2f} pts")
n, x, y = cnt["held-out"]
gap = 100 * (x - y) / n
print(f"unseen-customer gap {gap:.2f} pts -> {'PASS' if gap < 1.5 else 'FAIL'} (threshold 1.5)")
