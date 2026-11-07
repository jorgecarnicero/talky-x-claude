"""Big-pool AR receipts without usable references (POLICIES §2, §3, §5.5-5.6).

Pattern in the data structure: a clearly identified customer (IBAN / exact name) with many open invoices pays a batch
of them with no usable document reference. Customers pay in due-date order: the paid set is a contiguous run of the
customer's open invoices sorted by due date (stale old residuals before it are left alone) minus a few skipped invoices
(held / disputed / paid separately), optionally net of open credit notes (full positive rows, no exception, §2),
optionally with one invoice taking an early-payment discount within terms (SHORT_DISCOUNT, row = full open, §3).

Payer: exactly one customer by IBAN/exact alias (longest alias wins when short names collide); if several candidates
remain (<= 8), each is solved and the line is claimed only when exactly one candidate has an explanation.
Claim only when unique + passes a decoy-target significance test. Solver: bigpool_solver.py.
Uses ar_match's Matcher (ctx.cache['ar_matcher']) for payer identification.
"""
import os
import pickle
from collections import defaultdict

from data import norm, norm_words
from ar_index import IBAN_RE
from bigpool_solver import _c, solve_core, solve_multi

CONNECT = ("Y", "AND", "ET", "UND", "E", "I")
DUMP = os.environ.get("BP_DUMP", "")


def payer(m, t):
    """Return set of identified customer ids (IBAN / exact-name evidence)."""
    ix = m.ix
    s = m.text_words(t)
    words = [w for w in norm_words(s).split() if w not in CONNECT]
    by_name = {c for c, sc in m.names(t.entity, words).items() if sc >= 1.0 and c in ix.cust}
    if len(by_name) > 1:
        # several exact alias hits (short trade names inside longer names): keep the longest matched alias
        T = " " + " ".join(words) + " "
        best = defaultdict(int)
        for w in set(words):
            for core, cid, kind, tail in ix.alias.get((t.entity, w), ()):
                if cid in by_name and (" " + core + " ") in T:
                    best[cid] = max(best[cid], len(core))
        if best:
            top = max(best.values())
            by_name = {c for c, L in best.items() if L == top}
    by_iban = set()
    ibans = set(IBAN_RE.findall(s.upper()))
    if isinstance(t.counterparty_account, str) and t.counterparty_account:
        ibans.add(norm(t.counterparty_account))
    for ib in ibans:
        for c in ix.iban.get(norm(ib), ()):
            if c in ix.cust and ix.cust[c].entity == t.entity:
                by_iban.add(c)
    return by_name, by_iban


def pick_customer(by_name, by_iban):
    if by_iban and by_name:
        both = by_iban & by_name
        if len(both) == 1:
            return next(iter(both))
        return None
    s = by_iban or by_name
    if len(s) == 1:
        return next(iter(s))
    return None


def pool_of(m, ctx, cid, t):
    ix = m.ix
    inv, cn = [], []
    for i in ix.cust_items.get(cid, ()):
        v = ix.item[i]
        if v[1] != t.entity or v[6] != t.currency or v[4] > t.booking_date:
            continue
        o = ctx.open.get(i, 0.0)
        if o <= 0.005:
            continue
        if v[0] == "ARI":
            inv.append(i)
        elif v[0] == "ARC" and v[14] != "C":
            cn.append(i)
    inv.sort(key=lambda i: (ix.item[i][5], ix.item[i][4], ix.item[i][3]))
    cn.sort(key=lambda i: (ix.item[i][4], ix.item[i][3]))
    return inv, cn


def _tuples(ix, ctx, ids):
    return [(i, ix.item[i], ctx.open[i]) for i in ids]


def run(ctx):
    m = ctx.cache.get("ar_matcher")
    if m is None:
        print("    ar_bigpool: ar_matcher not in cache (run ar_match first)")
        return
    ix = m.ix
    stats = defaultdict(int)
    pend = ctx.pending()
    pend = pend[pend.amount > 0]
    dump = []
    for t in pend.itertuples(index=False):
        if ctx.done(t.bank_txn_id):
            continue
        by_name, by_iban = payer(m, t)
        cid = pick_customer(by_name, by_iban)
        if DUMP:
            cids = set(by_name) | set(by_iban)
            pools = {}
            for c in cids:
                inv, cn = pool_of(m, ctx, c, t)
                pools[c] = ([(i, ix.item[i], ctx.open[i]) for i in inv], [(i, ix.item[i], ctx.open[i]) for i in cn])
            dump.append((t.bank_txn_id, t.booking_date, float(t.amount), t.currency, t.entity, by_name, by_iban, cid,
                         pools))
            continue
        cands = by_name | by_iban
        if not cands:
            stats["no_payer"] += 1
            continue
        if cid is not None:
            inv, cn = pool_of(m, ctx, cid, t)
            rows, kind = solve_core(_c(t.amount), t.booking_date, _tuples(ix, ctx, inv), _tuples(ix, ctx, cn), ix.terms)
        else:
            pools = {}
            for c in cands:
                inv, cn = pool_of(m, ctx, c, t)
                pools[c] = (_tuples(ix, ctx, inv), _tuples(ix, ctx, cn))
            rows, kind = solve_multi(_c(t.amount), t.booking_date, pools, ix.terms)
        if rows is None:
            stats[kind] += 1
            continue
        res = rows, kind
        rows, kind = res
        ctx.allocate(t.bank_txn_id, rows, "bigpool:" + kind)
        stats[kind] += 1
    if DUMP:
        with open(DUMP, "wb") as fh:
            pickle.dump({"rows": dump, "terms": ix.terms}, fh)
        print(f"    ar_bigpool: dumped {len(dump)} lines to {DUMP}")
    print("    ar_bigpool:", dict(stats))
