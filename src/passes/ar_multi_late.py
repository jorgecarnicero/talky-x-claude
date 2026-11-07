"""n-to-m AR receipts (POLICIES §2-§3 PARTIAL) -- runs AFTER ar_match, on receipts still unexplained.

Pattern in the data structure: a customer pays several invoices in full and one more invoice only partly (40/50/60 %
of its total, the rest follows in a later transfer). Rows: the fully paid invoices at their open amount (no exception)
+ the partly paid invoice at the cash applied (PARTIAL).

Claims only when: the payer is identified by name (exact alias match, one customer), the customer's open invoices in
the line currency are few enough to enumerate (<= MAX_POOL), and exactly ONE combination
    sum(open of a subset of >= 1 invoices) + f * total(another fully-open invoice),  f in FRACTIONS
reproduces the cash to the cent. Uses ar_match's Matcher (ctx.cache['ar_matcher']) for payer identification.
"""
from collections import defaultdict
from itertools import combinations

from data import norm_words

FRACTIONS = (0.4, 0.5, 0.6)
MAX_POOL = 12
CONNECT = ("Y", "AND", "ET", "UND", "E", "I")


def cents(x):
    return int(round(float(x) * 100))


def run(ctx):
    m = ctx.cache.get("ar_matcher")
    if m is None:
        print("    ar_multi_late: ar_matcher not in cache (run ar_match first)")
        return
    ix = m.ix
    stats = defaultdict(int)
    pend = ctx.pending()
    pend = pend[pend.amount > 0]
    for t in pend.itertuples(index=False):
        if ctx.done(t.bank_txn_id):
            continue
        words = [w for w in norm_words(m.text_words(t)).split() if w not in CONNECT]
        cands = {c: s for c, s in m.names(t.entity, words).items() if s >= 1.0}
        if len(cands) != 1:
            stats["no_payer"] += 1
            continue
        cid = next(iter(cands))
        bdate = t.booking_date
        pool = [i for i in ix.cust_items.get(cid, ()) if ix.item[i][0] == "ARI" and ix.item[i][1] == t.entity
                and ix.item[i][6] == t.currency and ix.item[i][4] <= bdate and ctx.open.get(i, 0) > 0.005]
        if len(pool) < 2 or len(pool) > MAX_POOL:
            stats["pool"] += 1
            continue
        A = cents(t.amount)
        op = {i: cents(ctx.open[i]) for i in pool}
        # all subset sums of full payments: sum -> list of subsets
        sums = defaultdict(list)
        n = len(pool)
        for k in range(1, n + 1):
            for S in combinations(pool, k):
                sums[sum(op[i] for i in S)].append(S)
        sols = []
        for p in pool:
            tot = cents(ix.item[p][7])
            if op[p] != tot:          # partly paid already -> the fraction rule refers to the total
                continue
            for f in FRACTIONS:
                part = int(round(tot * f))
                for S in sums.get(A - part, ()):
                    if p not in S:
                        sols.append((S, p, part / 100.0))
            if len(sols) > 1:
                break
        if len(sols) != 1:
            stats["ambig" if sols else "nomatch"] += 1
            continue
        S, p, part = sols[0]
        rows = [{"item_id": i, "amount": ctx.open[i], "exception": ""} for i in S]
        rows.append({"item_id": p, "amount": part, "exception": "PARTIAL"})
        ctx.allocate(t.bank_txn_id, rows, "multi:n_to_m")
        stats["n_to_m"] += 1
    print("    ar_multi_late:", dict(stats))
