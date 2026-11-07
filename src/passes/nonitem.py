"""nonitem pass: bank lines that settle no item -> category (POLICIES §4).

1. BANK_REVERSAL pairs: the bank's "ANULACION OPERACION INDEBIDA N <yyyymmdd...>" line and the original wrong movement
   (same account, opposite amount, booked on the date encoded in the reference, else nearest date) -> both lines.
2. Keyword rules on the line's free text (text + remittance + counterparty), then clean bank-transaction-code families
   (camt BkTxCd / MT940 type) as fallback. See nonitem_rules.py.
Only lines whose category is certain are claimed; everything else is left to the item matchers.
"""
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import norm  # noqa: E402
from nonitem_rules import classify  # noqa: E402

REV_RX = re.compile(r"OPERACION INDEBIDA")
REF_RX = re.compile(r"INDEBIDA\s+N[º°O.]?\s*([\d\s|]{8,40})")


def _texts(df):
    cols = [c for c in ("text", "remittance_info", "counterparty_name") if c in df.columns]
    t = df[cols[0]].fillna("").astype(str)
    for c in cols[1:]:
        t = t + " | " + df[c].fillna("").astype(str)
    return t.str.upper()


def _reversals(ctx, pend, txt):
    """Return {bank_txn_id: 'BANK_REVERSAL'} for reversal lines and their originals."""
    out = {}
    bank = ctx.bank
    is_rev = txt.str.contains(REV_RX)
    if not is_rev.any():
        return out
    # candidate originals: any line of the window (pending), keyed by (account, cents)
    cents = (bank.amount * 100).round().astype("int64")
    key = {}
    pend_ids = set(pend.bank_txn_id)
    for i, (tid, acc, c, d) in enumerate(zip(bank.bank_txn_id, bank.account_id, cents, bank.booking_date)):
        if tid in pend_ids:
            key.setdefault((acc, int(c)), []).append((d, tid))
    for tid, acc, amt, d, t in zip(pend.bank_txn_id[is_rev], pend.account_id[is_rev], pend.amount[is_rev],
                                   pend.booking_date[is_rev], txt[is_rev]):
        out[tid] = "BANK_REVERSAL"
        m = REF_RX.search(t)
        ref_date = None
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if len(digits) >= 8:
                try:
                    ref_date = str(pd.Timestamp(f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}").date())
                except Exception:
                    ref_date = None
        cands = [(dd, ot) for dd, ot in key.get((acc, int(round(-amt * 100))), []) if ot != tid and ot not in out]
        if not cands:
            continue
        dt = pd.Timestamp(d)
        rd = pd.Timestamp(ref_date) if ref_date else None

        def dist(x):
            xd = pd.Timestamp(x[0])
            return (abs((xd - rd).days) if rd is not None else 0, abs((xd - dt).days))
        best = min(cands, key=dist)
        bd = pd.Timestamp(best[0])
        if (rd is not None and abs((bd - rd).days) <= 3) or (rd is None and abs((bd - dt).days) <= 10):
            out[best[1]] = "BANK_REVERSAL"
    return out


# markers of a returned collection (direct-debit R-transaction, unpaid cheque, chargeback): those re-open an item
RETURN_RX = re.compile(r"VTM-|MREF|\bRUM\b|RUECKLAST|IMPAYE|DEVOLUCION RECIBO|DEVOL\.? RECIBO|RETURN|UNPAID|RETOUR"
                       r"|\b(AC|AM|MD|MS|SL|AG|RR|RC|BE|FF)\d\d\b|\bR\d\d\b|CHARGEBACK|STORNO|RICHIAMO|INSOLUTO|IMPAGAD")


def _name_keys(df, cols):
    d = {}
    if df is None or not len(df):
        return d
    for c in cols:
        if c not in df.columns:
            continue
        for e, n in zip(df["entity"], df[c]):
            k = norm(n)[:18]
            if len(k) >= 8:
                d.setdefault(e, set()).add(k)
    return d


def _customer_refunds(ctx, pend, txt):
    """Outgoing transfer to one of our customers (not a supplier) for exactly the amount of a receipt booked on the same
    account in the previous 90 days = refund of a duplicate payment (POLICIES §3/§4)."""
    out = set()
    bank = ctx.bank
    cents = (bank.amount * 100).round().astype("int64")
    rec = {}
    for acc, c, d in zip(bank.account_id, cents, bank.booking_date):
        if c > 0:
            rec.setdefault((acc, int(c)), []).append(pd.Timestamp(d))
    cand = []
    for tid, acc, amt, d, t, ent in zip(pend.bank_txn_id, pend.account_id, pend.amount, pend.booking_date, txt,
                                        pend.entity):
        if amt >= 0 or ctx.done(tid):
            continue
        ds = rec.get((acc, int(round(-amt * 100))))
        if not ds:
            continue
        dd = pd.Timestamp(d)
        if not any(pd.Timedelta(0) < dd - x <= pd.Timedelta(days=90) for x in ds):
            continue
        if RETURN_RX.search(t):
            continue
        cand.append((tid, ent, amt, t))
    if not cand:
        return out
    m = ctx.masters
    ck = _name_keys(m.get("customers"), ["name", "trade_name"])
    sk = _name_keys(m.get("suppliers"), ["name"])
    # an open AR credit note of that amount may be "paid out" by transfer: that settles the ARC, not a refund
    it = ctx.items
    arc = it[(it.item_type == "ARC") & it.item_id.isin(set(ctx.open))]
    arc_keys = set(zip(arc.entity.astype(str), (pd.Series([ctx.open[i] for i in arc.item_id]) * 100).round().astype("int64")))
    for tid, ent, amt, t in cand:
        nt = norm(t.replace(" | ", ""))
        if not any(k in nt for k in ck.get(ent, ())):
            continue
        if any(k in nt for k in sk.get(ent, ())):
            continue
        if (ent, int(round(-amt * 100))) in arc_keys:
            continue
        out.add(tid)
    return out


def run(ctx):
    pend = ctx.pending()
    if not len(pend):
        return
    pend = pend.reset_index(drop=True)
    txt = _texts(pend).reset_index(drop=True)
    stats = {}
    for tid, cat in _reversals(ctx, pend, txt).items():
        if ctx.allocate(tid, [{"category": cat, "exception": ""}], "nonitem:rev"):
            stats["BANK_REVERSAL"] = stats.get("BANK_REVERSAL", 0) + 1
    codes = pend["bank_code"].fillna("").astype(str) if "bank_code" in pend.columns else pd.Series([""] * len(pend))
    for tid, t, amt, code in zip(pend.bank_txn_id, txt, pend.amount, codes):
        if ctx.done(tid):
            continue
        cat, how = classify(t, amt, code)
        if cat:
            ctx.allocate(tid, [{"category": cat, "exception": ""}], f"nonitem:{how}")
            stats[cat] = stats.get(cat, 0) + 1
    for tid in _customer_refunds(ctx, pend, txt):
        if ctx.allocate(tid, [{"category": "CUSTOMER_REFUND", "exception": ""}], "nonitem:refund"):
            stats["CUSTOMER_REFUND*"] = stats.get("CUSTOMER_REFUND*", 0) + 1
    ctx.cache["nonitem_stats"] = stats
    print("   nonitem:", dict(sorted(stats.items(), key=lambda x: -x[1])), flush=True)
