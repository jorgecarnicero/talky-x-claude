"""ar_payer (model/v5-matching): second chance for AR receipts that ar_match left unexplained.

Runs after ar_match and before v3rest. The payer is identified with payer_v5.PayerIndex (name variants scored one by
one, truncation, legal form, IBAN / payer-string history from earlier labelled lines); the amount is explained with
ar_match's own Matcher.try_customer (same subset / credit-note / PDF logic, nothing duplicated). Several plausible
payers are tried; the line is claimed only when the evidence is strong enough:

  * the best-scored payer explains the amount, with score >= S_TOP and no other explaining payer within MARGIN_TOP;
  * or exactly one payer explains it with score >= S_ONE (or it leads the next explaining payer by MARGIN), and only
    with an exact-type explanation (no subset search / partial heuristics for a payer that is not the best-scored).

Thresholds are calibrated on the dev window only (tools/run_dev.py). No labels of the reconciled period are used.
"""
import os
import time
from collections import defaultdict

from ar_index import find_month
from data import norm, norm_words

S_MIN = 0.55
S_TOP = 0.80
S_ONE = 0.75
MARGIN_TOP = 0.10
MARGIN = 0.25
MAX_TRY = 10
CONNECT = ("Y", "AND", "ET", "UND", "E", "I")
# explanations accepted for a payer that is not the best-scored candidate (exact amount evidence only)
EXACT_KINDS = ("single", "ref_exact", "ref_cn")


def _kind_ok(kind):
    return kind in EXACT_KINDS


def _try(m, cid, t, A, toks, grefs, words, month, evid):
    """explain the line with customer cid using ar_match's matcher (no state change)"""
    from passes import ar_match as AM
    m.budget = AM.MAX_NODES
    m.words_set = set(words)
    m.inst = None
    mm = AM.INST_RE.search(m.text_words(t).upper())
    if mm:
        k, n = int(mm.group(1)), int(mm.group(2))
        if 1 <= k <= n <= 24 and n >= 2:
            m.inst = (k, n)
    m.pre = {cid: m.cust_ctx(cid, t, t.booking_date, toks, grefs)}
    try:
        return m.try_customer(cid, t, A, t.currency, t.booking_date, toks, grefs, month, evid)
    except Exception:  # noqa: BLE001 (a failing explanation must not stop the pass)
        return None


def round_excess(ex, o):
    """the customer paid a round extra (POLICIES §3 OVERPAYMENT: one invoice, fully cleared): whole 1/2/5 x 10^k
    currency units, or an exact 1/2/5/10 % of the open amount. On TRAIN golden 2021-2025 every OVERPAYMENT line is a
    single invoice with such an excess; arbitrary excesses are almost always other cases (several invoices, discounts)."""
    ex = round(ex, 2)
    if ex >= 1 and abs(ex - round(ex)) < 0.005:
        v = int(round(ex))
        while v % 10 == 0:
            v //= 10
        if v in (1, 2, 5):
            return True
    return any(abs(ex - round(o * p, 2)) <= 0.011 for p in (0.01, 0.02, 0.05, 0.10))


def overpayment(m, ctx, t, A, cands):
    """strong, unrivalled payer + exactly one open invoice (line currency, issued by the booking date) with
    open < cash <= open * 1.105 and a round excess -> that invoice, cleared at its open amount, OVERPAYMENT"""
    cid, sc, _ = cands[0]
    if sc < S_TOP or (len(cands) > 1 and sc - cands[1][1] < MARGIN_TOP):
        return None
    it = m.ix.item
    hits = []
    for iid in m.ix.cust_items.get(cid, ()):
        v = it[iid]
        o = ctx.open.get(iid, 0.0)
        if v[0] != "ARI" or o <= 0.005 or v[6] != t.currency or v[4] > t.booking_date or v[1] != t.entity:
            continue
        if o < A - 0.005 and A <= o * 1.105 + 0.005 and round_excess(A - o, o):
            hits.append((iid, o))
    if len(hits) != 1:
        return None
    iid, o = hits[0]
    return [{"item_id": iid, "amount": round(o, 2), "exception": "OVERPAYMENT"}]


def run(ctx):
    import payer_v5 as P
    from passes import ar_match as AM
    t0 = time.time()
    m = ctx.cache.get("ar_matcher") or AM.Matcher(ctx)
    hist = P.build_history(ctx)
    ix = P.PayerIndex(ctx, hist)
    ctx.cache["payer_v5"] = ix
    print(f"   ar_payer: index {len(ix.variants)} customers, history {len(hist.get('iban', {}))} IBANs / "
          f"{len(hist.get('alias_key', {}))} payer strings (before {hist.get('start')})  [{time.time() - t0:.0f}s]")
    pend = ctx.pending()
    pend = pend[pend.amount > 0]
    stats = defaultdict(int)
    for t in pend.itertuples(index=False):
        if ctx.done(t.bank_txn_id):
            continue
        cands = [c for c in ix.candidates(t) if c[1] >= S_MIN][:MAX_TRY]
        if not cands:
            stats["no_cand"] += 1
            continue
        s = m.text_words(t)
        words = [w for w in norm_words(s).split() if w not in CONNECT]
        toks = m.tokens(s)
        grefs = m.global_refs(t.entity, toks)
        month = find_month(words)
        A = float(t.amount)
        expl = []
        for rank, (cid, sc, ev) in enumerate(cands):
            r = _try(m, cid, t, A, toks, grefs, words, month, 1.5 if rank == 0 and sc >= S_TOP else 1.0)
            if r:
                expl.append((sc, rank, cid, r))
                if len(expl) >= 3:
                    break
        if not expl:
            r = overpayment(m, ctx, t, A, cands) if os.environ.get("AR_PAYER_OVERPAY", "0") == "1" else None
            if r:
                ctx.allocate(t.bank_txn_id, r, "ar_payer:top:overpay_round")
                stats["overpay"] += 1
            else:
                stats["no_expl"] += 1
            continue
        sc, rank, cid, (rows, kind) = expl[0]
        second = expl[1][0] if len(expl) > 1 else None
        if rank == 0 and sc >= S_TOP and (second is None or sc - second >= MARGIN_TOP):
            tag = "top"
        elif sc >= S_ONE and _kind_ok(kind) and (second is None or sc - second >= MARGIN):
            tag = "amt"
        else:
            stats["ambiguous"] += 1
            continue
        seen, clean = set(), []
        for x in rows:
            if x["item_id"] not in seen:
                seen.add(x["item_id"])
                clean.append(x)
        ctx.allocate(t.bank_txn_id, clean, f"ar_payer:{tag}:{kind}")
        for x in clean:
            if x["item_id"] and x["item_id"] not in ctx.open:
                m.closed_by[x["item_id"]] = "payer_" + kind
        stats[tag] += 1
    print("   ar_payer:", dict(stats), f"[{time.time() - t0:.0f}s]")
