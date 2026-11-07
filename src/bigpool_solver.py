"""Solver for ar_bigpool: a contiguous run of the customer's open invoices in due-date order minus k skipped invoices
(+ the open return/price credit notes: none / all / one, + at most one early-payment discount within terms).

Acceptance (no labels, no period-specific tuning):
  * explanation unique at the smallest cost (cost = skipped invoices + 1 if a discount is used), k <= KMAX;
  * decoy test: the same search for 20 nearby wrong targets (A +/- q*0.37) finds nothing at that cost -> the exact-cent
    hit is not a coincidence of a dense pool;
  * >= 2 invoices paid (single-invoice explanations belong to ar_match).
Pure function over plain tuples so it can be evaluated offline.
Item tuple layout = ARIndex.item: (type, entity, party, doc, issue, due, ccy, total, terms, po, rel, ...).
"""
import os
from datetime import date, timedelta

KMAX = int(os.environ.get("BP_KMAX", "3"))
MIN_POOL = int(os.environ.get("BP_MIN_POOL", "3"))
MAX_POOL = 150
CN_SINGLE = os.environ.get("BP_CN_SINGLE", "1") == "1"
DISC = os.environ.get("BP_DISC", "1") == "1"


def _c(x):
    return int(round(float(x) * 100))


def _d(s):
    return date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def disc_delta(tup, op, bdate, terms):
    """Cents an invoice may be short by under an early-payment discount within terms (0 if not applicable)."""
    pct, dd = terms.get(tup[8], (0.0, 0))
    if not pct:
        return 0
    tot = tup[7]
    if abs(op - tot) > 0.005:
        return 0
    if bdate > (_d(tup[4]) + timedelta(days=dd + 3)).isoformat():
        return 0
    return _c(tot) - _c(round(tot - round(tot * pct / 100.0, 2), 2))


def solutions(A, bdate, inv, cn, terms, kmax=KMAX):
    """inv/cn: [(iid, tup, open)], inv sorted by (due, issue). Returns (cost, list of (paid_idx, cn_ids, disc_idx)).

    paid = prefix inv[:L] minus skipped; last prefix item always paid. cost = k + (1 if discount used).
    """
    n = len(inv)
    v = [_c(o) for _, _, o in inv]
    dl = [disc_delta(tp, o, bdate, terms) for _, tp, o in inv] if DISC else [0] * n
    cnv = [_c(o) for _, _, o in cn]
    cn_opts = [((), 0)]
    if cn:
        cn_opts.append((tuple(range(len(cn))), sum(cnv)))
        if CN_SINGLE and len(cn) > 1:
            for j in range(len(cn)):
                cn_opts.append(((j,), cnv[j]))
    found = {}   # cost -> set of solutions
    singles = {}          # value -> [idx] among items[0:L-1]
    pairs = {}            # value -> [(a,b)]
    P = 0
    for L in range(1, n + 1):
        last = L - 1
        P += v[last]
        if L >= 2:
            # add item L-2 to the skip-able set
            a = L - 2
            if kmax >= 2:
                for val, lst in list(singles.items()):
                    for b in lst:
                        pairs.setdefault(val + v[a], []).append((b, a))
            singles.setdefault(v[a], []).append(a)
        for cids, csum in cn_opts:
            base = P - A - csum   # sum of skipped (+ discount delta)
            if base < 0:
                continue
            # discount options: none, or one paid invoice j (j <= last)
            dopts = [(-1, 0)]
            if DISC:
                dopts += [(j, dl[j]) for j in range(L) if dl[j]]
            for j, dd in dopts:
                need = base - dd
                if need < 0:
                    continue
                c0 = (1 if j >= 0 else 0)
                if need == 0:
                    found.setdefault(c0, set()).add((tuple(range(L)), cids, j))
                    continue
                if kmax >= 1:
                    for s in singles.get(need, ()):
                        if s == j:
                            continue
                        found.setdefault(c0 + 1, set()).add((tuple(x for x in range(L) if x != s), cids, j))
                if kmax >= 2:
                    for s1, s2 in pairs.get(need, ()):
                        if j in (s1, s2):
                            continue
                        found.setdefault(c0 + 2, set()).add(
                            (tuple(x for x in range(L) if x not in (s1, s2)), cids, j))
                if kmax >= 3 and L >= 4:
                    for s1 in range(L - 1):
                        if s1 == j:
                            continue
                        for p in pairs.get(need - v[s1], ()):
                            if s1 < p[0] and j not in p:
                                found.setdefault(c0 + 3, set()).add(
                                    (tuple(x for x in range(L) if x not in (s1,) + p), cids, j))
    if not found:
        return None, []
    k = min(found)
    return k, sorted(found[k])


def solve_core(A, bdate, inv, cn, terms):
    """Stage 1: fully open invoices only (residuals of part-paid invoices are rarely in a batch payment);
    stage 2 (only when stage 1 finds no explanation at all): every open invoice, residuals included."""
    rows, why = _solve_core(A, bdate, inv, cn, terms, True)
    if rows is None and why == "nomatch" and TWO_STAGE:
        rows, why = _solve_core(A, bdate, inv, cn, terms, False)
        if rows is not None:
            why = "all_" + why
    return rows, why


def _solve_core(A, bdate, inv, cn, terms, full_only):
    if CN_FILTER:
        # §5.9: return/price credit notes reference their invoice -> nettable while that invoice is still open;
        # rappel credit notes are offset or paid out per their PDF (not known here) -> not netted blindly.
        open_docs = {tp[3]: tp[7] for _, tp, _ in inv}
        cn = [x for x in cn if x[1][14] != "R" and x[1][10] in open_docs]
        if CN_CANC:
            # a credit note for the full total of its invoice cancels it (§5.7: never settled by bank); netting it
            # adds a zero-sum pair that the amount cannot confirm
            cn = [x for x in cn if abs(open_docs[x[1][10]] - x[1][7]) > 0.005]
    if full_only:
        inv = [x for x in inv if abs(x[2] - x[1][7]) < 0.005]
    if DUE_LO is not None:
        # customers pay what is due around the payment date: the newest paid invoice is due near the booking date
        lo = (_d(bdate) - timedelta(days=DUE_LO)).isoformat()
        hi = (_d(bdate) + timedelta(days=DUE_HI)).isoformat()
        inv = [x for x in inv if x[1][5] <= hi]
        END_OK[0] = lo
    else:
        END_OK[0] = ""
    if len(inv) > MAX_POOL and TRUNC:
        # very long ledgers: stale old items are never paid in a batch; search the newest MAX_POOL invoices
        inv = inv[-MAX_POOL:]
    if len(inv) < MIN_POOL or len(inv) > MAX_POOL:
        return None, "pool"
    k, sols = (solutions_window if WIN else solutions)(A, bdate, inv, cn, terms)
    if not sols:
        return None, "nomatch"
    if len(sols) != 1:
        return None, "ambig"
    if k > MAX_COST:
        return None, "cost"
    paid, cids, j = sols[0]
    if NULL_N:
        # significance test: the same search on nearby (wrong) targets must find nothing at the same cost; if decoy
        # targets also get hit, the pool is dense enough that an exact-cent hit is likely a coincidence.
        fn = solutions_window if WIN else solutions
        hits = 0
        for q in range(1, NULL_N + 1):
            for off in (q * 37, -q * 37):
                kk, ss = fn(A + off, bdate, inv, cn, terms, kmax=min(k, KMAX))
                if ss and kk <= k:
                    hits += 1
        if hits > NULL_MAX:
            return None, "dense"
    if not full_only and sum(1 for x in paid if abs(inv[x][2] - inv[x][1][7]) > 0.005) > 2:
        return None, "residuals"
    if len(paid) < 2:
        return None, "single"   # single-invoice explanations are ar_match's job
    rows = []
    for x in paid:
        iid, tp, o = inv[x]
        rows.append({"item_id": iid, "amount": o, "exception": "SHORT_DISCOUNT" if x == j else ""})
    for y in cids:
        iid, tp, o = cn[y]
        rows.append({"item_id": iid, "amount": o, "exception": ""})
    kind = f"k{k}" + ("_cn" if cids else "") + ("_disc" if j >= 0 else "")
    return rows, kind


CN_FILTER = os.environ.get("BP_CN_FILTER", "1") == "1"
DUE_LO = int(os.environ["BP_DUE_LO"]) if os.environ.get("BP_DUE_LO") else None
DUE_HI = int(os.environ.get("BP_DUE_HI", "21"))
END_OK = [""]
NULL_N = int(os.environ.get("BP_NULL_N", "10"))
NULL_MAX = int(os.environ.get("BP_NULL_MAX", "0"))
K3_POOL = int(os.environ.get("BP_K3_POOL", "60"))
TWO_STAGE = os.environ.get("BP_TWO_STAGE", "1") == "1"
TRUNC = os.environ.get("BP_TRUNC", "0") == "1"
CN_ALL_COST = int(os.environ.get("BP_CN_ALL_COST", "0"))
CN_ONE_COST = int(os.environ.get("BP_CN_ONE_COST", "0"))
MAX_COST = int(os.environ.get("BP_MAX_COST", "99"))
CN_CANC = os.environ.get("BP_CN_CANC", "1") == "1"
WIN = os.environ.get("BP_WIN", "1") == "1"
FULL_ONLY = os.environ.get("BP_FULL_ONLY", "1") == "1"


def solutions_window(A, bdate, inv, cn, terms, kmax=KMAX):
    """Paid set = a contiguous run inv[s..e] (due order) minus k interior skipped invoices; endpoints paid.

    Stale residuals at the head of the ledger (old, never paid) make a strict prefix model fail; the run of paid
    invoices is contiguous in due order apart from a few held invoices. cost = k (+1 with a discount).
    """
    n = len(inv)
    v = [_c(o) for _, _, o in inv]
    dl = [disc_delta(tp, o, bdate, terms) for _, tp, o in inv] if DISC else [0] * n
    dpos = [j for j in range(n) if dl[j]]
    cnv = [_c(o) for _, _, o in cn]
    cn_opts = [((), 0, 0)]
    if cn:
        cn_opts.append((tuple(range(len(cn))), sum(cnv), CN_ALL_COST))
        if CN_SINGLE and len(cn) > 1:
            for j in range(len(cn)):
                cn_opts.append(((j,), cnv[j], CN_ONE_COST))
    singles, pairs = {}, {}
    for a in range(n):
        singles.setdefault(v[a], []).append(a)
    if kmax >= 2:
        for a in range(n):
            for b in range(a + 1, n):
                pairs.setdefault(v[a] + v[b], []).append((a, b))
    pre = [0]
    for x in v:
        pre.append(pre[-1] + x)
    found = {}

    def add(cost, s, e, skip, cids, j):
        if len(found) and cost > min(found) + 0:
            return
        found.setdefault(cost, set()).add((tuple(x for x in range(s, e + 1) if x not in skip), cids, j))

    targets = []
    for cids, csum, ccost in cn_opts:
        targets.append((A + csum, cids, ccost))
    for s in range(n):
        for e in range(s + 1, n):
            if END_OK[0] and inv[e][1][5] < END_OK[0]:
                continue
            W = pre[e + 1] - pre[s]
            for T, cids, ccost in targets:
                base = W - T
                if base < 0:
                    continue
                dopts = [(-1, 0)] + [(j, dl[j]) for j in dpos if s <= j <= e]
                for j, dd in dopts:
                    need = base - dd
                    if need < 0:
                        continue
                    c0 = (1 if j >= 0 else 0) + ccost
                    if need == 0:
                        add(c0, s, e, (), cids, j)
                        continue
                    if kmax >= 1:
                        for x in singles.get(need, ()):
                            if s < x < e and x != j:
                                add(c0 + 1, s, e, (x,), cids, j)
                    if kmax >= 2:
                        for a, b in pairs.get(need, ()):
                            if s < a and b < e and j not in (a, b):
                                add(c0 + 2, s, e, (a, b), cids, j)
                    if kmax >= 3 and c0 == 0 and n <= K3_POOL and (not found or min(found) >= 3):
                        for x in range(s + 1, e):
                            for a, b in pairs.get(need - v[x], ()):
                                if x < a and b < e:
                                    add(3, s, e, (x, a, b), cids, j)
    if not found:
        return None, []
    k = min(found)
    return k, sorted(found[k])


MULTI_MAX = int(os.environ.get("BP_MULTI_MAX", "8"))


def solve_multi(A, bdate, pools, terms):
    """Payer named ambiguously (several customers share the matched alias): solve each candidate; accept only when
    exactly one candidate has any explanation and that explanation passes all checks (the others find nothing)."""
    if len(pools) > MULTI_MAX:
        return None, "multi_many"
    res = []
    for cid, (inv, cn) in pools.items():
        rows, why = solve_core(A, bdate, inv, cn, terms)
        if rows is not None:
            res.append((rows, why))
        elif why not in ("nomatch", "pool", "all_nomatch"):
            return None, "multi_" + why
    if len(res) != 1:
        return None, "multi_none" if not res else "multi_ambig"
    rows, why = res[0]
    return rows, "multi_" + why
