"""Offline simulation of exception rules on the lines a submission leaves UNIDENTIFIED (dev dataset only).

    python tools/exc_sim.py runs/v5/sub.csv.gz

Ledger at each line = opening balance (open_items_at_start + invoices issued in the phase) minus what the submission
itself allocated on earlier lines (booking order) -> the state the pipeline would see. Payer evidence = payer_v5
(history before the phase). Rules are scored with the OFFICIAL scorer; the golden is never used to decide.
"""
import csv
import gzip
import os
import sys
from collections import defaultdict
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
D_ = os.path.expanduser("~/hackdata/data_dev")


def round_excess(ex, o):
    """customer paid a round extra: whole 1/2/5 x 10^k units, or an exact 1/2/5/10 % of the open amount"""
    ex = round(ex, 2)
    if abs(ex - round(ex)) < 0.005 and ex >= 1:
        v = int(round(ex))
        while v % 10 == 0:
            v //= 10
        if v in (1, 2, 5):
            return True
    return any(abs(ex - round(o * p, 2)) <= 0.011 for p in (0.01, 0.02, 0.05, 0.10))


def main():
    sub = sys.argv[1]
    os.environ["BANKREC_DATA"] = D_
    os.environ["BANKREC_CACHE"] = os.path.join(D_, "solution", "cache", "v35")
    import pandas as pd
    import data as D
    import payer_v5 as P
    rows = list(csv.DictReader(gzip.open(sub, "rt", encoding="utf-8")))
    by_txn = defaultdict(list)
    for r in rows:
        by_txn[r["bank_txn_id"]].append(r)
    unid = {t for t, rs in by_txn.items() if not rs[0]["item_id"] and rs[0]["exception"] == "UNIDENTIFIED"}
    bank = D.load_bank(D_, "live").sort_values(["booking_date", "bank_txn_id"])
    masters = D.load_masters(D_, "live")
    items = pd.concat([D.load_items(D_, p) for p in ("train", "live")], ignore_index=True)
    items = items.drop_duplicates("item_id", keep="last")
    ctx = SimpleNamespace(root=D_, bank=bank, masters=masters, items=items)
    ix = P.PayerIndex(ctx, P.build_history(ctx))
    start = bank.booking_date.min()
    oi = D._read(os.path.join(D_, "live", "open_items_at_start.csv.gz"))
    open_ = dict(zip(oi.item_id, oi.open_amount.astype(float)))
    ar = items[items.item_type.astype(str) == "ARI"]
    for iid, tot, isd in zip(ar.item_id, ar.total, ar.issue_date):
        if isd >= start:
            open_[iid] = float(tot)
    meta = {iid: (pid, cur, isd) for iid, pid, cur, isd in zip(ar.item_id, ar.party_id, ar.currency, ar.issue_date)}
    party_items = defaultdict(list)
    for iid, (pid, cur, isd) in meta.items():
        if iid in open_:
            party_items[pid].append(iid)
    proposals = {}
    for t in bank.itertuples(index=False):
        tid = t.bank_txn_id
        if tid in unid and t.amount > 0:
            c = ix.candidates(t)
            if c and c[0][1] >= 0.8 and (len(c) == 1 or c[0][1] - c[1][1] >= 0.1):
                A = float(t.amount)
                hits = [i for i in party_items.get(c[0][0], ()) if open_.get(i, 0) > 0.005
                        and meta[i][1] == t.currency and meta[i][2] <= t.booking_date
                        and open_[i] < A - 0.005 and A <= open_[i] * 1.105 + 0.005]
                hits = [i for i in hits if round_excess(A - open_[i], open_[i])]
                if len(hits) == 1:
                    i = hits[0]
                    proposals[tid] = [(i, round(open_[i], 2), "OVERPAYMENT")]
                    open_[i] = 0.0
                    continue
        for r in by_txn.get(tid, ()):  # consume the ledger with the submission's own allocations
            if r["item_id"] and r["item_id"] in open_:
                if r["exception"] == "RETURNED":
                    open_[r["item_id"]] += float(r["amount"])
                elif r["exception"] in ("PARTIAL", "SHORT_DISPUTE"):
                    open_[r["item_id"]] -= float(r["amount"])
                elif r["exception"] != "DUPLICATE_PAYMENT":
                    open_[r["item_id"]] = 0.0
    print("overpayment proposals:", len(proposals))
    sys.path.insert(0, os.path.join(REPO, "..", "talky-harness", "starter"))
    import bankrec_score as S
    exc = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
           "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]
    cats = [r["category"] for r in csv.DictReader(open(os.path.join(D_, "masters", "categories.csv"), encoding="utf-8"))]
    g = S.load_golden(os.path.join(REPO, "runs", "golden_dev.csv.gz"), os.path.join(D_, "live", "bank_transactions.csv.gz"),
                      exc, cats)

    def score(use):
        out = ["bank_txn_id,item_id,amount,category,exception"]
        for tid, rs in by_txn.items():
            if tid in use:
                out += [f"{tid},{i},{a:.2f},,{e}" for i, a, e in use[tid]]
            else:
                out += [",".join([r["bank_txn_id"], r["item_id"], r["amount"], r["category"], r["exception"]]) for r in rs]
        res = S.score_submission(g, "\n".join(out).encode(), "x.csv")
        return res["score"], res["metrics"]
    print("baseline:", score({})[0])
    sc, m = score(proposals)
    print("with overpayment rule:", sc, {k: m[k] for k in ("precision", "recall", "txn_accuracy", "exception_macro_f1")})
    with open(os.path.join(REPO, "runs", "overpay_proposals.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bank_txn_id", "item_id", "amount", "exception"])
        for tid, rs in proposals.items():
            for i, a, e in rs:
                w.writerow([tid, i, a, e])


if __name__ == "__main__":
    main()
