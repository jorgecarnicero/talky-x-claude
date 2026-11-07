"""exc_overpay: unreferenced OVERPAYMENT on receipts still answered UNIDENTIFIED (runs after exc_fix, before cancel_guard).

POLICIES §3 OVERPAYMENT: the customer paid more than the open amount of a SINGLE invoice; the invoice is cleared
(amount = open amount). The pipeline cannot see an invoice reference on these lines, but the excess has a structure no
unrelated invoice has: it is a round amount (a whole 1 or 2, or a whole multiple of 5) or a round share of the invoice
(1 % / 10 % of its open amount).

Claims a line only when ALL hold (information the pipeline itself has; no golden):
  - the line is a receipt answered UNIDENTIFIED (nothing else explained it);
  - the payer is identified by reference / IBAN / name (ar_match's Matcher, top-3 customers), and among the open ARIs
    of those customers (same entity and currency, issued by the booking date, open amount from a chronological replay of our
    own answers) exactly ONE has cash - open that is a round excess (0 < excess <= open amount).
Rows: that invoice, amount = open amount, exception OVERPAYMENT.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOPN = 3                 # customers considered per line
PCTS = (0.01, 0.10)      # round shares of the open amount
CENT_TOL = 0.011


def round_excess(ex, op):
    if ex < 0.99 or ex > op:
        return False
    if abs(ex - round(ex)) < 0.0051:
        e = int(round(ex))
        if e in (1, 2) or e % 5 == 0:
            return True
    return any(abs(ex - round(op * p, 2)) <= CENT_TOL for p in PCTS)


def payer_candidates(m, t):
    """-> customers ranked by the pipeline's own identification (reference > IBAN > name)."""
    import passes.ar_match as am
    from ar_index import IBAN_RE
    from data import norm, norm_words
    ent = t.entity
    s = m.text_words(t)
    words = [w for w in norm_words(s).split() if w not in am.CONNECT]
    cand = defaultdict(float)
    for iid, st in m.global_refs(ent, m.tokens(s)).items():
        pid = m.ix.item[iid][2]
        cand[pid] = max(cand[pid], 2.0 + st / 10)
    ibans = set(IBAN_RE.findall(s.upper()))
    if isinstance(t.counterparty_account, str) and t.counterparty_account:
        ibans.add(norm(t.counterparty_account))
    for ib in ibans:
        for cid in m.ix.iban.get(norm(ib), ()):
            if cid in m.ix.cust and m.ix.cust[cid].entity == ent:
                cand[cid] = max(cand[cid], 2.5)
    for cid, sc in m.names(ent, words).items():
        cand[cid] = max(cand[cid], 1.0 + sc)
    return sorted(cand, key=lambda c: -cand[c])[:TOPN]


def run(ctx):
    if os.environ.get("EXC_OVERPAY", "1") == "0":
        print("    exc_overpay: disabled (EXC_OVERPAY=0)")
        return
    from passes.exc_fix import opening
    m = ctx.cache.get("ar_matcher")
    if m is None:
        print("    exc_overpay: ar_matcher not in cache (run ar_match first)")
        return
    op = dict(opening(ctx))
    n = 0
    for t in ctx.bank.itertuples():
        rows = ctx.results.get(t.bank_txn_id)
        if not rows:
            continue
        if rows[0]["item_id"]:
            for r in rows:                       # chronological replay of our own answers (Ctx.allocate semantics)
                iid, e, a = r["item_id"], r["exception"], float(r["amount"])
                if e == "RETURNED":
                    op[iid] = op.get(iid, 0.0) + a
                elif e in ("PARTIAL", "SHORT_DISPUTE"):
                    op[iid] = op.get(iid, 0.0) - a
                elif e != "DUPLICATE_PAYMENT":
                    op[iid] = 0.0
            continue
        if t.amount <= 0 or rows[0].get("exception") != "UNIDENTIFIED":
            continue
        hits = []
        for c in payer_candidates(m, t):
            for i in m.ix.cust_items.get(c, ()):
                v = m.ix.item[i]
                o = op.get(i, 0.0)
                if v[0] == "ARI" and v[1] == t.entity and v[6] == t.currency and v[4] <= t.booking_date \
                        and o > 0.005 and round_excess(t.amount - o, o):
                    hits.append((i, o))
        if len(hits) == 1:
            i, o = hits[0]
            ctx.results[t.bank_txn_id] = [{"item_id": i, "amount": round(o, 2), "category": "", "exception": "OVERPAYMENT"}]
            ctx.source[t.bank_txn_id] = "exc_overpay"
            op[i] = 0.0
            n += 1
    print(f"    exc_overpay: {n} unreferenced overpayments")
