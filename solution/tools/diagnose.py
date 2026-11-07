"""Show wrong bank lines of some segments: bank line, golden rows, our rows and the matcher that fired.

    python solution/bankrec.py --phase sample --out sub_sample.csv.gz --why why_sample.csv
    python solution/tools/diagnose.py sub_sample.csv.gz why_sample.csv ar_ref_noise,ap_confirming [--n 5]
"""
import argparse
import csv
import gzip
import random
from collections import Counter, defaultdict


def rd(p):
    return csv.DictReader(gzip.open(p, "rt", encoding="utf-8") if p.endswith(".gz") else open(p, encoding="utf-8"))


def key(rows):
    return sorted((r["item_id"], round(float(r["amount"] or 0), 2), r["category"], r["exception"]) for r in rows)


def same(a, b):
    if len(a) != len(b):
        return False
    return all(x[0] == y[0] and x[2] == y[2] and x[3] == y[3] and abs(x[1] - y[1]) <= max(0.01, 0.005 * y[1])
               for x, y in zip(a, b))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("submission")
    ap.add_argument("why")
    ap.add_argument("segments", help="comma separated segment names (see golden_allocations 'segment')")
    ap.add_argument("--phase", default="sample", choices=["sample", "train"])
    ap.add_argument("--n", type=int, default=4)
    a = ap.parse_args()
    D = a.phase + "/"
    bt = {r["bank_txn_id"]: r for r in rd(D + "bank_transactions.csv.gz")}
    why = {r["bank_txn_id"]: r["why"] for r in rd(a.why)}
    S, G, seg = defaultdict(list), defaultdict(list), {}
    for r in rd(a.submission):
        S[r["bank_txn_id"]].append(r)
    for r in rd(D + "golden_allocations.csv.gz"):
        G[r["bank_txn_id"]].append(r)
        seg[r["bank_txn_id"]] = r["segment"]
    random.seed(0)
    for s in a.segments.split(","):
        total = [t for t in G if seg[t] == s]
        wrong = [t for t in total if not same(key(S[t]), key(G[t]))]
        print(f"\n##### {s}: wrong {len(wrong)}/{len(total)}  by matcher: {Counter(why.get(t) for t in wrong).most_common(6)}")
        for t in random.sample(wrong, min(a.n, len(wrong))):
            b = bt[t]
            print(f"- {t} {b['account_id']} {b['booking_date']} amt={b['amount']} {b['currency']} why={why.get(t)}")
            print(f"   text={b['text'][:160]!r} rmt={b['remittance_info'][:60]!r} cp={b['counterparty_name']!r}")
            print("   GOLD:", [(r["item_id"] or r["category"], r["amount"], r["exception"]) for r in G[t]][:6], len(G[t]))
            print("   OURS:", [(r["item_id"] or r["category"], r["amount"], r["exception"]) for r in S[t]][:6], len(S[t]))


if __name__ == "__main__":
    main()
