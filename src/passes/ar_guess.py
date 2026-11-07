"""Best-guess allocation for AR receipts still unmatched after every precise pass (runs after ar_dispute, before v3rest).

Why: allocation F1 is over (line, item) pairs. An extra pair that is right with probability p raises F1 iff
p > F1/2 (~0.47), and a receipt with an identified payer whose golden has items is wrong as UNIDENTIFIED anyway.
So for receipts whose payer is ONE identified customer (IBAN / exact alias, same evidence as ar_bigpool) we output the
most plausible explanation instead of giving up, when its kind has empirical pair precision >= GUESS_TAU.

Candidate kinds (first that applies, same currency as the bank line):
  exact1   one open invoice with open == cash (several: oldest by issue date, §5.6 FIFO)
  run      bigpool solver explanation (due-date run minus skips, +credit notes, +discount), first at min cost,
           ambiguity / decoy test ignored
  over     one open invoice the cash exceeds by <= 100 (whole amount) -> OVERPAYMENT
  prefix   due-date prefix of the open invoices (credit notes netted when it helps) whose sum is closest to the cash,
           relative residual <= GUESS_PREFIX_REL -> full rows, no exception
  partial  cash below the oldest open invoice -> PARTIAL on it, amount = cash
Kind precisions are measured offline on TRAIN windows (see scratch_guess/); a kind is emitted only when listed in
GUESS_KINDS. Flag: pass is a no-op unless AR_GUESS=1.
"""
import os
from collections import defaultdict
from datetime import date, timedelta

from passes.ar_bigpool import payer, pick_customer
from bigpool_solver import _c, _d, solutions as bp_solutions

ON = os.environ.get("AR_GUESS", "0") == "1"
KINDS = set(os.environ.get("GUESS_KINDS", "exact1,over,window").split(","))
WIN_H = int(os.environ.get("GUESS_WIN_H", "-3"))
MIN_WIN = int(os.environ.get("GUESS_MIN_WIN", "5"))
PREFIX_REL = float(os.environ.get("GUESS_PREFIX_REL", "0.02"))
MAX_POOL = int(os.environ.get("GUESS_MAX_POOL", "3000"))
MIN_PREFIX = int(os.environ.get("GUESS_MIN_PREFIX", "1"))
CNX = os.environ.get("GUESS_CNX", "1") == "1"
MULTI = os.environ.get("GUESS_MULTI", "1") == "1"
ONLY_UNID = os.environ.get("GUESS_ONLY_UNID", "1") == "1"
# GUESS_V3_ITEMS=1: also guess on lines where v3 answers with items (override v3's weak item answers); v3 category
# answers (CUSTOMER_ADVANCE, INTEREST_INCOME...) are never pre-empted
V3_ITEMS = os.environ.get("GUESS_V3_ITEMS", "0") == "1"


def build_pool(ix, open_, cid, entity, ccy, bdate):
    inv, cn = [], []
    for i in ix.cust_items.get(cid, ()):
        v = ix.item[i]
        if v[1] != entity or v[6] != ccy or v[4] > bdate:
            continue
        o = open_.get(i, 0.0)
        if o <= 0.005:
            continue
        if v[0] == "ARI":
            inv.append((i, v, o))
        elif v[0] == "ARC" and v[14] != "C":
            cn.append((i, v, o))
    inv.sort(key=lambda x: (x[1][5], x[1][4], x[1][3]))
    cn.sort(key=lambda x: (x[1][4], x[1][3]))
    return inv, cn


def guess(A, bdate, inv, cn, terms, kinds=None):
    """A: cash in cents. Returns (kind, rows) or (None, None)."""
    kinds = KINDS if kinds is None else kinds
    if not inv:
        return None, None
    # exact single invoice (FIFO by issue date among equal amounts)
    eq = [x for x in inv if _c(x[2]) == A]
    if eq:
        if "exact1" not in kinds:
            return None, None
        x = min(eq, key=lambda x: (x[1][4], x[1][3]))
        return "exact1", [{"item_id": x[0], "amount": x[2], "exception": ""}]
    # due-date run (bigpool solver, no uniqueness / decoy requirement)
    if len(inv) <= 150:
        open_docs = {tp[3] for _, tp, _ in inv}
        cnf = [x for x in cn if x[1][14] != "R" and x[1][10] in open_docs]
        k, sols = bp_solutions(A, bdate, inv, cnf, terms, kmax=2 if len(inv) > 60 else 3)
        if sols and "run" in kinds:
            paid, cids, j = sols[0]
            rows = [{"item_id": inv[p][0], "amount": inv[p][2], "exception": "SHORT_DISCOUNT" if p == j else ""}
                    for p in paid]
            rows += [{"item_id": cnf[q][0], "amount": cnf[q][2], "exception": ""} for q in cids]
            return "run", rows
    # overpayment of one invoice by a whole amount
    ov = [x for x in inv if 0 < A - _c(x[2]) <= 10000 and (A - _c(x[2])) % 100 == 0]
    if ov and "over" in kinds:
        x = min(ov, key=lambda x: (x[1][4], x[1][3]))
        return "over", [{"item_id": x[0], "amount": x[2], "exception": "OVERPAYMENT"}]
    # due-date window: invoices still fully open and due by booking date + WIN_H days, taken newest-due first
    # until they cover the cash (customers pay the batch that just fell due; skipped/held invoices make this a
    # ~60 % pair-precision guess, which raises F1 when the window has >= MIN_WIN invoices)
    if "window" in kinds:
        lim = (_d(bdate) + timedelta(days=WIN_H)).isoformat()
        cand = [x for x in inv if x[1][5] <= lim and abs(x[2] - x[1][7]) < 0.005]
        if CNX:
            # invoices with a credit note against them are often held (39 % vs 64 % paid on TRAIN 2024-10..11)
            rel = {x[1][10] for x in cn}
            cand = [x for x in cand if x[1][3] not in rel]
        S, pick = 0, []
        for x in reversed(cand):
            if S >= A * 0.995:
                break
            pick.append(x)
            S += _c(x[2])
        if len(pick) >= MIN_WIN and S >= A * 0.995:
            return "window", [{"item_id": x[0], "amount": x[2], "exception": ""} for x in reversed(pick)]
    # closest due-date prefix (off by default: 19 % pair precision on TRAIN 2024-10..11)
    best = None
    cnv = sum(_c(o) for _, _, o in cn)
    P = 0
    for L, x in enumerate(inv, 1):
        P += _c(x[2])
        for use_cn in ((False, True) if cn else (False,)):
            S = P - (cnv if use_cn else 0)
            r = abs(S - A)
            if best is None or r < best[0]:
                best = (r, L, use_cn)
        if P - cnv > A * 1.5:
            break
    if best and "prefix" in kinds and best[1] >= MIN_PREFIX and best[0] <= PREFIX_REL * A:
        r, L, use_cn = best
        rows = [{"item_id": x[0], "amount": x[2], "exception": ""} for x in inv[:L]]
        if use_cn:
            rows += [{"item_id": x[0], "amount": x[2], "exception": ""} for x in cn]
        return "prefix", rows
    if "partial" in kinds:
        x = inv[0]
        if A < _c(x[2]):
            return "partial", [{"item_id": x[0], "amount": A / 100.0, "exception": "PARTIAL"}]
    return None, None


def run(ctx):
    if not ON:
        print("    ar_guess: off (AR_GUESS=1 to enable)")
        return
    m = ctx.cache.get("ar_matcher")
    if m is None:
        print("    ar_guess: ar_matcher not in cache")
        return
    ix = m.ix
    stats = defaultdict(int)
    pend = ctx.pending()
    pend = pend[pend.amount > 0]
    v3 = ctx.cache.get("v3")
    if v3 is None and os.environ.get("V3_SUB"):
        from passes.v3import import _load
        v3 = _load(ctx)
    v3 = v3 or {}
    for t in pend.itertuples(index=False):
        if ctx.done(t.bank_txn_id):
            continue
        if ONLY_UNID:
            # never pre-empt a v3 category answer (CUSTOMER_ADVANCE, INTEREST_INCOME...): only lines v3 leaves
            # UNIDENTIFIED (or does not answer)
            r3 = v3.get(t.bank_txn_id)
            if r3 and r3[0].get("exception") != "UNIDENTIFIED" and not (V3_ITEMS and r3[0].get("item_id")):
                stats["v3_answer"] += 1
                continue
        by_name, by_iban = payer(m, t)
        cid = pick_customer(by_name, by_iban)
        cands = by_name | by_iban
        if cid is None and MULTI and 2 <= len(cands) <= 8:
            # several candidate customers: window guess only, claimed when exactly one candidate yields one
            hits = []
            for c in cands:
                inv, cn = build_pool(ix, ctx.open, c, t.entity, t.currency, t.booking_date)
                if inv and len(inv) <= MAX_POOL:
                    k, rows = guess(_c(t.amount), t.booking_date, inv, cn, ix.terms, {"window"})
                    if rows:
                        hits.append(rows)
            if len(hits) == 1:
                ctx.allocate(t.bank_txn_id, hits[0], "guess:multi_window")
                stats["multi_window"] += 1
            else:
                stats["multi_%s" % ("none" if not hits else "ambig")] += 1
            continue
        if cid is None:
            stats["no_payer"] += 1
            continue
        inv, cn = build_pool(ix, ctx.open, cid, t.entity, t.currency, t.booking_date)
        if not inv or len(inv) > MAX_POOL:
            stats["no_pool" if not inv else "big_pool"] += 1
            continue
        kind, rows = guess(_c(t.amount), t.booking_date, inv, cn, ix.terms)
        if rows is None:
            stats["none"] += 1
            continue
        ctx.allocate(t.bank_txn_id, rows, "guess:" + kind)
        stats[kind] += 1
    print("    ar_guess:", dict(stats))
