"""Why do receipts with items end UNIDENTIFIED? (dev only; golden used to DIAGNOSE). Reuses exc_sim's ledger."""
import csv, gzip, os, sys
from collections import Counter, defaultdict
from types import SimpleNamespace
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
D_ = os.path.expanduser("~/hackdata/data_dev")
os.environ["BANKREC_DATA"] = D_
os.environ["BANKREC_CACHE"] = os.path.join(D_, "solution", "cache", "v35")
import pandas as pd
import data as D
import payer_v5 as P
sub = sys.argv[1]
rows = list(csv.DictReader(gzip.open(sub, "rt", encoding="utf-8")))
by_txn = defaultdict(list)
for r in rows:
    by_txn[r["bank_txn_id"]].append(r)
unid = {t for t, rs in by_txn.items() if not rs[0]["item_id"] and rs[0]["exception"] == "UNIDENTIFIED"}
G = defaultdict(list)
for r in csv.DictReader(open(os.path.join(D_, "golden_dev.csv"), encoding="utf-8")):
    if r["item_id"]:
        G[r["bank_txn_id"]].append((r["item_id"], float(r["amount"]), r["exception"]))
bank = D.load_bank(D_, "live").sort_values(["booking_date", "bank_txn_id"])
items = pd.concat([D.load_items(D_, p) for p in ("train", "live")], ignore_index=True).drop_duplicates("item_id", keep="last")
ctx = SimpleNamespace(root=D_, bank=bank, masters=D.load_masters(D_, "live"), items=items)
ix = P.PayerIndex(ctx, P.build_history(ctx))
oi = D._read(os.path.join(D_, "live", "open_items_at_start.csv.gz"))
open_ = dict(zip(oi.item_id, oi.open_amount.astype(float)))
start = bank.booking_date.min()
for iid, tot, isd in zip(items.item_id, items.total, items.issue_date):
    if isd >= start:
        open_[iid] = float(tot)
party = dict(zip(items.item_id, items.party_id))
c = Counter(); ex = defaultdict(list)
for t in bank.itertuples(index=False):
    tid = t.bank_txn_id
    g = G.get(tid)
    if tid in unid and g and t.amount > 0:
        gp = {party.get(i) for i, _, _ in g}
        cand = ix.candidates(t)
        ranked = [p for p, _, _ in cand]
        rank = min((ranked.index(p) + 1 for p in gp if p in ranked), default=None)
        topsc = cand[0][1] if cand else 0
        all_open = all(open_.get(i, 0) > 0.005 for i, _, _ in g)
        exact = abs(sum(a for _, a, _ in g) - t.amount) < 0.02
        multi_party = len(gp) > 1
        k = ("multi-party" if multi_party else ("payer rank " + ("1" if rank == 1 else ("2-10" if rank and rank <= 10 else ("11+" if rank else "none")))),
             "items open" if all_open else "some items closed", "sum==A" if exact else "sum!=A")
        c[k] += 1
        if len(ex[k]) < 3:
            ex[k].append((tid, round(topsc, 2), len(g), t.text[:60]))
    for r in by_txn.get(tid, ()):
        if r["item_id"] and r["item_id"] in open_:
            e = r["exception"]
            if e == "RETURNED": open_[r["item_id"]] += float(r["amount"])
            elif e in ("PARTIAL", "SHORT_DISPUTE"): open_[r["item_id"]] -= float(r["amount"])
            elif e != "DUPLICATE_PAYMENT": open_[r["item_id"]] = 0.0
for k, v in c.most_common():
    print(v, k, ex[k][:2])
