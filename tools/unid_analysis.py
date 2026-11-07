"""Offline study: keep the UNIDENTIFIED flag only where the payer cannot be identified (dev dataset).

    python tools/unid_analysis.py runs/v5/sub.csv.gz --data ~/hackdata/data_dev

For every no-item UNIDENTIFIED line of the submission, compute the payer_v5 evidence (best candidate score, name / IBAN /
alias parts) with exactly the inference-time index (history before the phase start). Then rescore the submission with
the OFFICIAL scorer for several rules "drop the flag when evidence >= x". Writes runs/unid_features.csv.
The golden is only used by the scorer.
"""
import argparse
import csv
import gzip
import os
import sys
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sub")
    ap.add_argument("--data", default=os.path.expanduser("~/hackdata/data_dev"))
    ap.add_argument("--harness", default=os.path.join(REPO, "..", "talky-harness"))
    a = ap.parse_args()
    d = os.path.abspath(a.data)
    os.environ["BANKREC_DATA"] = d
    os.environ["BANKREC_CACHE"] = os.path.join(d, "solution", "cache", "v35")
    import pandas as pd
    import data as D
    import payer_v5 as P

    rows = list(csv.DictReader(gzip.open(a.sub, "rt", encoding="utf-8")))
    unid = {r["bank_txn_id"] for r in rows if not r["item_id"] and r["exception"] == "UNIDENTIFIED"}
    bank = D.load_bank(d, "live")
    masters = D.load_masters(d, "live")
    items = pd.concat([D.load_items(d, p) for p in ("train", "live")], ignore_index=True)
    items = items.drop_duplicates("item_id", keep="last")
    ctx = SimpleNamespace(root=d, bank=bank, masters=masters, items=items)
    hist = P.build_history(ctx)
    ix = P.PayerIndex(ctx, hist)
    from collections import defaultdict
    oi = D._read(os.path.join(d, "live", "open_items_at_start.csv.gz"))
    open_by = defaultdict(list)
    for pid, typ in zip(oi.party_id, oi.item_type):
        if typ == "ARI":
            open_by[pid].append(1)
    new = items[(items.item_type == "ARI") & (items.issue_date >= bank.booking_date.min())]
    new_by = defaultdict(list)
    for pid, dt in zip(new.party_id, new.issue_date):
        new_by[pid].append(dt)
    feats = {}
    for t in bank[bank.bank_txn_id.isin(unid)].itertuples(index=False):
        c = ix.candidates(t)
        top = c[0] if c else (None, 0.0, {"name": 0, "iban": 0, "alias": 0})
        second = c[1][1] if len(c) > 1 else 0.0
        # open AR items of the best payer known at the booking date (opening balance + invoices issued since)
        n_open = len(open_by.get(top[0], ())) + sum(1 for x in new_by.get(top[0], ()) if x <= t.booking_date)             if top[0] else 0
        feats[t.bank_txn_id] = (top[1], top[2]["name"], top[2]["iban"], top[2]["alias"], second, n_open)
    with open(os.path.join(REPO, "runs", "unid_features.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bank_txn_id", "top", "name", "iban", "alias", "second", "n_open"])
        for k, v in feats.items():
            w.writerow([k, *v])
    # official scorer on the window
    sys.path.insert(0, os.path.join(a.harness, "starter"))
    import bankrec_score as S
    exc = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
           "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]
    cats = [r["category"] for r in csv.DictReader(open(os.path.join(d, "masters", "categories.csv"), encoding="utf-8"))]
    gd = os.path.join(d, "golden_dev.csv")
    gz = os.path.join(REPO, "runs", "golden_dev.csv.gz")
    if not os.path.exists(gz):
        with open(gd, "rb") as fi, gzip.open(gz, "wb") as fo:
            fo.write(fi.read())
    g = S.load_golden(gz, os.path.join(d, "live", "bank_transactions.csv.gz"), exc, cats)

    def score(rule):
        out = ["bank_txn_id,item_id,amount,category,exception"]
        for r in rows:
            e = r["exception"]
            if r["bank_txn_id"] in feats and e == "UNIDENTIFIED" and rule(feats[r["bank_txn_id"]]):
                e = ""
            out.append(",".join([r["bank_txn_id"], r["item_id"], r["amount"], r["category"], e]))
        res = S.score_submission(g, "\n".join(out).encode(), "x.csv")
        m = res["metrics"]
        return res["score"], m["exception_macro_f1"], m["txn_accuracy"]

    print("lines flagged UNIDENTIFIED:", len(feats))
    print("baseline (unchanged):", score(lambda f: False))
    strong = lambda f: f[3] >= 0.5 or f[2] >= 0.5 or f[0] >= 1.2  # noqa: E731
    print("alias|iban|top>=1.2:", sum(map(strong, feats.values())), score(strong))
    for x in (0.75, 0.85, 1.0, 1.2):
        r = lambda f, x=x: f[0] >= x and f[5] > 0  # noqa: E731
        print(f"top>={x} & payer has open items: n={sum(map(r, feats.values()))}", score(r))
        r2 = lambda f, x=x: (f[0] >= x and f[5] > 0) or f[3] >= 0.5 or f[2] >= 0.5  # noqa: E731
        print(f"  ... or alias/iban: n={sum(map(r2, feats.values()))}", score(r2))
    for x in (0.85, 1.0):
        r = lambda f, x=x: f[0] >= x and f[5] >= 2  # noqa: E731
        print(f"top>={x} & >=2 open items: n={sum(map(r, feats.values()))}", score(r))

if __name__ == "__main__":
    main()
