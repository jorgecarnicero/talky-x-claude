"""Still-unmatched AR receipts explained by a withholding or a dispute deduction (POLICIES §3).

Runs AFTER ar_bigpool (before v3rest) on receipts no earlier pass claimed.

  * SHORT_WITHHOLDING: the payer (IBAN / exact name, same evidence as ar_bigpool) is a customer in a withholding
    country (MX/BR/MA, §3). The cash equals a set of its open invoices (same currency) each net of the SAME rate
    (1.25 / 1.5 / 10 % of the invoice total, rounded per invoice; cent tolerance per row). Every invoice row: full open
    amount, SHORT_WITHHOLDING.
  * SHORT_DISPUTE (one invoice cut): cash = a set of full open invoices of the payer, except one invoice paid net of a
    clean deduction (3/5/10/20/30 % of its open amount). Full rows: no exception; cut row: amount = cash paid for it,
    SHORT_DISPUTE (the disputed part stays open, §3).

Search: exact-cent meet-in-the-middle over the payer's open invoices (<= 22 open invoices).
The dispute-cut search is OFF by default (DISP_CUT=1): on TRAIN 2024-10..11 its 8 claims were all wrong (late
discounts / n-to-m lines), and the real one-cut lines are 40-600-invoice batches out of reach of this search. A line is claimed only when exactly
ONE explanation exists over all rates / cut invoices (and no plain full-payment subset explains it).
"""
import os
from collections import defaultdict

from passes.ar_bigpool import payer, pick_customer

WH_COUNTRIES = {"MX", "BR", "MA"}
WH_RATES = (0.0125, 0.015, 0.10)
CUT_RATES = (0.03, 0.05, 0.10, 0.20, 0.30)
MAX_POOL = int(os.environ.get("DISP_MAX_POOL", "22"))
CUT_POOL = int(os.environ.get("DISP_CUT_POOL", "18"))
DO_WH = os.environ.get("DISP_WH", "1") == "1"
DO_CUT = os.environ.get("DISP_CUT", "0") == "1"   # TRAIN 2024-10..11: 0/8 exact -> off
WH_POOL = int(os.environ.get("DISP_WH_POOL", "22"))


def _c(x):
    return int(round(x * 100))


def _sums(vals):
    """all subset sums of vals (list of int cents) -> dict sum -> list of bitmasks (capped at 2)."""
    out = {0: [0]}
    for k, v in enumerate(vals):
        new = {}
        bit = 1 << k
        for s, ms in out.items():
            new.setdefault(s, []).extend(ms)
            l2 = new.setdefault(s + v, [])
            for m in ms:
                if len(l2) < 2:
                    l2.append(m | bit)
        for s in new:
            if len(new[s]) > 2:
                new[s] = new[s][:2]
        out = new
    return out


def solutions(vals, target, tol, cap=3):
    """subsets (as index tuples) of vals with |sum - target| <= tol; at most `cap` returned."""
    n = len(vals)
    h = n // 2
    L, R = vals[:h], vals[h:]
    sl, sr = _sums(L), _sums(R)
    res = []
    for s, ms in sl.items():
        for d in range(-tol, tol + 1):
            mr = sr.get(target - s + d)
            if not mr:
                continue
            for a in ms:
                for b in mr:
                    idx = tuple([i for i in range(h) if a >> i & 1] + [h + i for i in range(n - h) if b >> i & 1])
                    if idx and idx not in res:
                        res.append(idx)
                        if len(res) >= cap:
                            return res
    return res


def subsets_multi(vals, targets):
    """yield (idx, j, paid) for subsets of vals summing exactly to a target key whose (j, paid) has j in the subset
    and the subset has >= 2 elements. Stops after 2 hits."""
    n = len(vals)
    h = n // 2
    sl, sr = _sums(vals[:h]), _sums(vals[h:])
    hits = 0
    for s, ms in sl.items():
        for T, lst in targets.items():
            mr = sr.get(T - s)
            if not mr:
                continue
            for a in ms:
                for b in mr:
                    idx = [i for i in range(h) if a >> i & 1] + [h + i for i in range(n - h) if b >> i & 1]
                    if len(idx) < 2:
                        continue
                    for j, paid in lst:
                        if j in idx:
                            yield idx, j, paid
                            hits += 1
                            if hits >= 2:
                                return


def _pool(m, ctx, cid, t):
    ix = m.ix
    inv = []
    for i in ix.cust_items.get(cid, ()):
        v = ix.item[i]
        if v[0] != "ARI" or v[1] != t.entity or v[6] != t.currency or v[4] > t.booking_date:
            continue
        o = ctx.open.get(i, 0.0)
        if o > 0.005:
            inv.append(i)
    inv.sort(key=lambda i: (ix.item[i][5], ix.item[i][4], ix.item[i][3]))
    return inv


def run(ctx):
    m = ctx.cache.get("ar_matcher")
    if m is None:
        print("    ar_dispute: ar_matcher not in cache (run ar_match first)")
        return
    ix = m.ix
    stats = defaultdict(int)
    pend = ctx.pending()
    pend = pend[pend.amount > 0]
    for t in pend.itertuples(index=False):
        if ctx.done(t.bank_txn_id):
            continue
        by_name, by_iban = payer(m, t)
        cid = pick_customer(by_name, by_iban)
        if cid is None:
            stats["no_payer"] += 1
            continue
        inv = _pool(m, ctx, cid, t)
        if not inv:
            stats["no_pool"] += 1
            continue
        ctry = getattr(ix.cust.get(cid), "country", "")
        wh = DO_WH and ctry in WH_COUNTRIES
        if not wh and (not DO_CUT or len(inv) > MAX_POOL):
            stats["not_wh" if len(inv) <= MAX_POOL else "big_pool"] += 1
            continue
        if wh and len(inv) > WH_POOL:
            # chance exact-cent subsets grow with the pool (truncating to the 30 oldest: 12/14 exact) -> skip
            stats["big_pool"] += 1
            continue
        A = _c(t.amount)
        op = {i: ctx.open[i] for i in inv}
        # a plain full-payment subset explains the line -> not ours
        full = [_c(op[i]) for i in inv]
        if solutions(full, A, 0, cap=1):
            stats["plain"] += 1
            continue
        found = []
        if wh:
            for r in WH_RATES:
                # only invoices still fully open can carry a withholding of their total
                elig = [i for i in inv if abs(op[i] - ix.item[i][7]) < 0.005]
                vals = [_c(op[i]) - _c(round(ix.item[i][7] * r, 2)) for i in elig]
                for S in solutions(vals, A, 0, cap=2):
                    found.append(("wh", [elig[k] for k in S], None, r))
        if DO_CUT and len(found) <= 1 and len(inv) <= CUT_POOL:  # experimental, off
            # A + cut(j,p) == sum of a full-open subset containing j (and at least one other invoice)
            tg = defaultdict(list)
            for j, ij in enumerate(inv):
                for p in CUT_RATES:
                    cut = _c(round(op[ij] * p, 2))
                    tg[A + cut].append((j, full[j] - cut))
            for S in subsets_multi(full, tg):
                idx, j, paid = S
                found.append(("cut", [inv[k] for k in idx if k != j], inv[j], paid))
                if len(found) > 1:
                    break
        if len(found) != 1:
            stats["none" if not found else "ambig"] += 1
            continue
        kind, S, j, x = found[0]
        if kind == "wh":
            rows = [{"item_id": i, "amount": op[i], "exception": "SHORT_WITHHOLDING"} for i in S]
        else:
            rows = [{"item_id": i, "amount": op[i], "exception": ""} for i in S]
            rows.append({"item_id": j, "amount": x / 100.0, "exception": "SHORT_DISPUTE"})
        ctx.allocate(t.bank_txn_id, rows, f"disp:{kind}{len(rows)}")
        stats[kind] += 1
    print("    ar_dispute:", dict(stats))
