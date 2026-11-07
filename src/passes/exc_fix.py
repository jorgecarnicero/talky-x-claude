"""exc_fix: last pass. Re-derives exceptions/amounts of already-answered AR item rows from POLICIES §2-3, using a
chronological replay of our own answers over the opening ledger (open amount of each item BEFORE each bank line).

Rules (only on receipts, single-invoice lines, bank currency == invoice currency, source not in TRUSTED):
  R1 duplicate: a DUPLICATE_PAYMENT row whose item was still open before the line is a normal payment
     ('' if it clears the item, PARTIAL with amount = cash otherwise). (e.g. transfer after a returned direct debit)
  R2 remainder/partial consistency: PARTIAL whose cash clears the remaining open balance -> '' (remainder after a
     partial / last instalment / dispute settlement); '' whose cash leaves an open balance -> PARTIAL (amount = cash).
  R3 retention: invoice whose PDF says a % is retained, first payment = total - retention -> PARTIAL (amount = cash)
     instead of SHORT_WITHHOLDING / SHORT_BANK_CHARGES / ''.
  R4 credit note netted (adds a row): single invoice marked PARTIAL whose shortfall equals exactly one still-open
     credit note of the same customer -> invoice '' (full open amount) + credit note row (full amount, no exception).
  R5 (off by default) unreferenced overpayment: a receipt answered UNIDENTIFIED whose payer (name/IBAN) is exactly one
     customer having exactly one open invoice (same currency) that the cash exceeds by a whole amount (1..100) ->
     OVERPAYMENT on that invoice, amount = open amount.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TRUSTED = ("batches", "remittance")          # ~99.8 % precise on exceptions: never touched
FULL = ("", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "FX_DIFFERENCE", "OVERPAYMENT")
ENABLE = {"R1": True, "R2": True, "R3": True, "R4": True,
          "R5": os.environ.get("EXC_R5", "0") == "1"}   # R5 answers UNIDENTIFIED lines (outside the 'rewrite only' contract): lead decides
ROUND_EXCESS_MAX = 100.0


def _tol(x):
    return max(0.011, 0.002 * abs(x))


def opening(ctx):
    if "open0" in ctx.cache:
        return ctx.cache["open0"]
    if getattr(ctx, "open0", None) is not None:
        return ctx.open0
    d0 = ctx.bank.booking_date.min() if len(ctx.bank) else None
    return ctx._opening(d0, True)


def replay(bank_ids, results, open0):
    """-> {(txn, item): open amount before that line}, following Ctx.allocate semantics in booking order."""
    op = dict(open0)
    before = {}
    for t in bank_ids:
        for r in results.get(t, ()):
            iid = r["item_id"]
            if not iid:
                continue
            o = op.get(iid, 0.0)
            before[(t, iid)] = o
            e, a = r["exception"], float(r["amount"])
            if e == "RETURNED":
                op[iid] = o + a
            elif e == "DUPLICATE_PAYMENT":
                pass
            elif e in ("PARTIAL", "SHORT_DISPUTE"):
                op[iid] = o - a
            else:
                op[iid] = 0.0
    return before, op


def rewrite(bank, results, source, items, open0, pdf, stats=None):
    """Core logic (pure): mutates results. items: DataFrame indexed by item_id with ITEM_COLS."""
    stats = {} if stats is None else stats
    ids = list(bank.bank_txn_id)
    before, op_end = replay(ids, results, open0)
    ret = pdf.get("ret") or {}
    typ = items.item_type.astype(str)
    cur = items.currency.astype(str)
    tot = items.total
    party = items.party_id
    used = {r["item_id"] for rows in results.values() for r in rows if r["item_id"]}
    # still-open credit notes by party (for R4)
    arc_by_party = {}
    no_offset = {k for k, v in (pdf.get("cnmode") or {}).items() if v == "refund"} | \
        {k for k, v in (pdf.get("rappel") or {}).items() if v == "paid_out"}
    if ENABLE["R4"]:
        arc = items[(typ == "ARC")]
        for iid, pid in zip(arc.item_id, arc.party_id):
            if iid in no_offset:
                continue
            if iid not in used and op_end.get(iid, 0.0) > 0.005:
                arc_by_party.setdefault(pid, []).append(iid)

    def bump(k):
        stats[k] = stats.get(k, 0) + 1

    for t, A, ccy in zip(bank.bank_txn_id, bank.amount, bank.currency):
        rows = results.get(t)
        if not rows or not rows[0]["item_id"] or A <= 0:
            continue
        src = source.get(t, "")
        if src.startswith(TRUSTED):
            continue
        if len(rows) != 1:
            continue
        r = rows[0]
        iid = r["item_id"]
        if iid not in typ.index or typ[iid] != "ARI" or cur[iid] != ccy:
            continue
        e = r["exception"]
        o = before.get((t, iid), 0.0)
        c = float(A)
        tl = _tol(c)
        # R1 duplicate of an item that was still open
        if ENABLE["R1"] and e == "DUPLICATE_PAYMENT" and o > 0.005 and c <= o + tl:
            if abs(c - o) <= tl:
                r["exception"], r["amount"] = "", round(o, 2)
            else:
                r["exception"], r["amount"] = "PARTIAL", round(c, 2)
            bump("R1")
            continue
        if o <= 0.005:
            continue
        # R3 retention: first payment = payable amount
        rt = ret.get(iid)
        if ENABLE["R3"] and rt and e in ("", "SHORT_WITHHOLDING", "SHORT_BANK_CHARGES", "SHORT_DISCOUNT") \
                and abs(o - tot[iid]) <= 0.011 and c < o - tl:
            pay = rt.get("payable") or (tot[iid] - (rt.get("amount") or 0.0))
            if pay and abs(c - pay) <= tl:
                r["exception"], r["amount"] = "PARTIAL", round(c, 2)
                bump("R3")
                continue
        # R2 consistency of PARTIAL / ''
        if ENABLE["R2"]:
            if e == "PARTIAL" and abs(c - o) <= tl:
                r["exception"], r["amount"] = "", round(o, 2)
                bump("R2_to_empty")
                continue
            if e == "" and c < o - tl and abs(float(r["amount"]) - c) <= tl:
                r["exception"], r["amount"] = "PARTIAL", round(c, 2)
                bump("R2_to_partial")
                continue
        # R4 credit note netted
        if ENABLE["R4"] and e == "PARTIAL" and c < o - tl:
            short = round(o - c, 2)
            cands = [k for k in arc_by_party.get(party[iid], ()) if cur[k] == ccy
                     and abs(op_end.get(k, 0.0) - short) <= 0.011]
            if len(cands) > 1:
                rel = [k for k in cands if items.related_document[k] in (iid, items.doc_number[iid])]
                cands = rel if len(rel) == 1 else []
            if len(cands) == 1:
                k = cands[0]
                r["exception"], r["amount"] = "", round(o, 2)
                rows.append({"item_id": k, "amount": round(op_end[k], 2), "category": "", "exception": ""})
                arc_by_party[party[iid]].remove(k)
                bump("R4")
                continue
    if ENABLE["R5"]:
        _overpay(bank, results, source, items, open0, stats)
    return stats


def _overpay(bank, results, source, items, open0, stats):
    from ar_index import core_name
    from ar_payer import payer_name
    import pandas as pd
    cu = items.attrs.get("customers")
    if cu is None:
        return
    idx, iban = {}, {}
    for r in cu.itertuples():
        for n in (r.name, r.trade_name):
            if isinstance(n, str) and n:
                idx.setdefault((r.entity, core_name(n)), set()).add(r.customer_id)
        if isinstance(r.iban, str) and r.iban:
            iban[r.iban] = r.customer_id
    typ = items.item_type.astype(str)
    ari = items[typ == "ARI"]
    cur = items.currency.astype(str)
    op = dict(open0)
    byp = {}
    for iid, pid in zip(ari.item_id, ari.party_id):
        if op.get(iid, 0.0) > 0.005:
            byp.setdefault(pid, []).append(iid)
    for t in bank.itertuples():
        rows = results.get(t.bank_txn_id)
        if not rows:
            continue
        if rows[0]["item_id"]:
            for r in rows:
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
        cn = core_name(payer_name(t) or "")
        cids = set(idx.get((t.entity, cn), ())) if cn else set()
        acc = t.counterparty_account if isinstance(t.counterparty_account, str) else ""
        if not cids and acc in iban:
            cids = {iban[acc]}
        if len(cids) != 1:
            continue
        cid = next(iter(cids))
        hits = []
        for i in byp.get(cid, ()):
            o = op.get(i, 0.0)
            x = t.amount - o
            if o > 0.005 and cur[i] == t.currency and 0.995 <= x <= ROUND_EXCESS_MAX + 0.005 \
                    and abs(x - round(x)) < 0.005:
                hits.append(i)
        if len(hits) == 1:
            i = hits[0]
            results[t.bank_txn_id] = [{"item_id": i, "amount": round(op[i], 2), "category": "", "exception": "OVERPAYMENT"}]
            source[t.bank_txn_id] = "exc_fix:over"
            op[i] = 0.0
            stats["R5"] = stats.get("R5", 0) + 1


def run(ctx):
    try:
        import pdfinfo as P
        pdf = {"ret": P.retention(ctx), "cnmode": P.cn_mode(ctx), "rappel": P.rappel_mode(ctx)}
    except Exception as ex:  # noqa: BLE001
        print(f"    exc_fix: no pdf info ({ex})")
        pdf = {}
    if ENABLE["R5"]:
        ctx.items.attrs["customers"] = ctx.masters.get("customers")
    stats = rewrite(ctx.bank, ctx.results, ctx.source, ctx.items, opening(ctx), pdf)
    print(f"    exc_fix: {stats}")
