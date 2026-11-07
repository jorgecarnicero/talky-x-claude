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
          "R5": os.environ.get("EXC_R5", "0") == "1",
          "R6": os.environ.get("EXC_R6", "0") == "1",
          "R7": os.environ.get("EXC_R7", "0") == "1",
          "R8": os.environ.get("EXC_R8", "1") == "1",
          "R9": os.environ.get("EXC_R9", "1") == "1",
          "R10": os.environ.get("EXC_R10", "1") == "1"}
SETTLE_WORDS = {"SETTLEMENT", "REGULARIZACION", "REGULARISATION", "REGOLARIZZAZIONE", "REGULARIZACAO"}   # R5 answers UNIDENTIFIED lines (outside the 'rewrite only' contract): lead decides
ROUND_EXCESS = (1.0, 2.0, 5.0, 10.0, 50.0, 100.0)   # TRAIN history: every round overpayment is one of these
# review finding: R2 reverses deliberate policy readings of ar_match 'settle' (disputed remainder, no exception) and
# ar_pdf instalments (out-of-order last instalment). EXC_R2_EXEMPT=1 skips R2 for those sources.
R2_EXEMPT = os.environ.get("EXC_R2_EXEMPT", "0") == "1"


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

    ents = dict(zip(bank.bank_txn_id, zip(bank.entity, bank.booking_date)))
    settle = set()
    if ENABLE["R10"]:
        from data import norm_words
        cols = [c for c in ("text", "ref1", "ref2", "remittance_info") if c in bank.columns]
        for row in zip(bank.bank_txn_id, *[bank[c] for c in cols]):
            words = set(norm_words(" ".join(x for x in row[1:] if isinstance(x, str))).split())
            if words & SETTLE_WORDS:
                settle.add(row[0])
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
        # R10 settlement of a disputed remainder: never PARTIAL (TRAIN: 0 PARTIAL rows on settlement-word lines)
        if t in settle:
            if e == "PARTIAL":
                r["exception"], r["amount"] = "", round(c, 2)
                bump("R10")
            continue
        # R8 bank charges: cross-border receipt (payer country != entity country), shortfall <= 60 EUR and <= 2 %
        if ENABLE["R8"] and e in ("PARTIAL", "") and c < o - 0.005 and _is_bank_charge(iid, ents[t], ccy, c, o, items):
            r["exception"], r["amount"] = "SHORT_BANK_CHARGES", round(o, 2)
            bump("R8")
            continue
        # R2 consistency of PARTIAL / ''
        r2_exempt = R2_EXEMPT and (src == "ar:settle" or src.startswith("ar_pdf_"))
        if ENABLE["R2"] and not r2_exempt:
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
    if ENABLE["R9"]:
        _replaced_swap(bank, results, items, open0, pdf.get("repl") or {}, stats)
    if ENABLE["R5"]:
        _overpay(bank, results, source, items, open0, stats)
    if ENABLE["R6"]:
        _fx_rematch(bank, results, source, items, open0, stats)
    return stats


def _overpay(bank, results, source, items, open0, stats):
    from ar_index import core_name
    from ar_payer import payer_name
    import pandas as pd
    cu = items.attrs.get("customers")
    if cu is None:
        return
    from data import norm
    idx, iban, full = {}, {}, {}
    for r in cu.itertuples():
        for n in (r.name, r.trade_name):
            if isinstance(n, str) and n:
                idx.setdefault((r.entity, core_name(n)), set()).add(r.customer_id)
                full.setdefault(r.entity, {}).setdefault(norm(n), set()).add(r.customer_id)
        if isinstance(r.iban, str) and r.iban:
            iban.setdefault(r.iban, set()).add(r.customer_id)
    typ = items.item_type.astype(str)
    ari = items[typ == "ARI"]
    cur = items.currency.astype(str)
    op = dict(open0)
    byp = {}
    for iid, pid in zip(ari.item_id, ari.party_id):
        if op.get(iid, 0.0) > 0.005:
            byp.setdefault(pid, []).append(iid)
    byp_all = {}
    if ENABLE.get("R7"):
        sub = items[typ.isin(["ARI", "ARC"])]
        for iid, pid in zip(sub.item_id, sub.party_id):
            if op.get(iid, 0.0) > 0.005:
                byp_all.setdefault(pid, []).append(iid)
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
        # payer: known paying IBAN first, then full (normalised) name incl. truncation / trailing noise,
        # then the core name. Several candidates are fine as long as exactly ONE open invoice fits.
        pn = payer_name(t) or ""
        acc = t.counterparty_account if isinstance(t.counterparty_account, str) else ""
        cids = set(iban.get(acc, ()))
        if not cids and pn:
            npn = norm(pn)
            fe = full.get(t.entity, {})
            cids = set(fe.get(npn, ()))
            if not cids and len(npn) >= 12:
                for k, v in fe.items():
                    if len(k) >= 12 and (k.startswith(npn) or npn.startswith(k)):
                        cids |= v
            if not cids:
                cids = set(idx.get((t.entity, core_name(pn)), ()))
        if not cids or len(cids) > 30:
            continue
        hits = []
        for cid in cids:
            for i in byp.get(cid, ()):
                o = op.get(i, 0.0)
                x = t.amount - o
                if o > 0.005 and cur[i] == t.currency and any(abs(x - v) < 0.005 for v in ROUND_EXCESS):
                    hits.append(i)
        if len(hits) != 1 and ENABLE.get("R7") and len(cids) == 1:
            rows7 = _disc_subset(next(iter(cids)), t, byp_all, op, typ, cur, items)
            if rows7:
                results[t.bank_txn_id] = rows7
                source[t.bank_txn_id] = "exc_fix:disc"
                for r in rows7:
                    op[r["item_id"]] = 0.0 if r["exception"] != "PARTIAL" else op[r["item_id"]] - r["amount"]
                stats["R7"] = stats.get("R7", 0) + 1
                continue
        if len(hits) == 1:
            i = hits[0]
            results[t.bank_txn_id] = [{"item_id": i, "amount": round(op[i], 2), "category": "", "exception": "OVERPAYMENT"}]
            source[t.bank_txn_id] = "exc_fix:over"
            op[i] = 0.0
            stats["R5"] = stats.get("R5", 0) + 1


WHT = (0.0125, 0.015, 0.10)


def _fx_subset(its, oc, target, op, typ, cur, items):
    """Receipt in another currency whose instructed (original) amount, in the invoice currency, equals: all open items
    of the payer in that currency / all but one / oldest-first prefix; optionally net of withholding. Unique only."""
    its = [i for i in its if cur[i] == oc and op.get(i, 0.0) > 0.005]
    if not its or len(its) > 200:
        return None
    its.sort(key=lambda i: (items.issue_date[i], i))
    val = {i: (op[i] if typ[i] == "ARI" else -op[i]) for i in its}
    cands = [its] + [[j for j in its if j != i] for i in its if typ[i] == "ARI"]
    cands += [its[:k] for k in range(1, len(its))]
    found = set()
    for S in cands:
        if not S or not any(typ[i] == "ARI" for i in S):
            continue
        tot = sum(val[i] for i in S)
        if abs(tot - target) <= 0.011:
            found.add(tuple(S))
            continue
        ari = sum(op[i] for i in S if typ[i] == "ARI")
        cn = sum(op[i] for i in S if typ[i] == "ARC")
        for r in WHT:
            wh = sum(round(items.total[i] * r, 2) for i in S if typ[i] == "ARI")
            if abs(ari - wh - cn - target) <= 0.011 * max(1, len(S)):
                found.add(tuple(S))
    if len(found) != 1:
        return None
    S = next(iter(found))
    return [{"item_id": i, "amount": round(op[i], 2), "category": "",
             "exception": "FX_DIFFERENCE" if typ[i] == "ARI" else ""} for i in S]


def _fx_rematch(bank, results, source, items, open0, stats):
    """R6: receipt whose bank line carries the instructed amount in another currency (orig_amount/orig_currency).
    If our current items do not explain orig_amount but exactly one set of the payer's open items does (payer = the
    customer of the items we picked, or a unique full-name match for UNIDENTIFIED lines), use that set."""
    from ar_payer import payer_name
    from data import norm
    _, op_end = replay(list(bank.bank_txn_id), results, open0)
    typ = items.item_type.astype(str)
    cur = items.currency.astype(str)
    used = {}
    for t, rows in results.items():
        for r in rows:
            if r["item_id"]:
                used.setdefault(r["item_id"], set()).add(t)
    byp = {}
    sub = items[typ.isin(["ARI", "ARC"])]
    for iid, pid in zip(sub.item_id, sub.party_id):
        if op_end.get(iid, 0.0) > 0.005 and iid not in used:
            byp.setdefault(pid, []).append(iid)
    cu = items.attrs.get("customers")
    full = {}
    if cu is not None:
        for r in cu.itertuples():
            for n in (r.name, r.trade_name):
                if isinstance(n, str) and n:
                    full.setdefault(r.entity, {}).setdefault(norm(n), set()).add(r.customer_id)
    before, _ = replay(list(bank.bank_txn_id), results, open0)
    for t in bank.itertuples():
        oc = t.orig_currency if isinstance(t.orig_currency, str) else ""
        if t.amount <= 0 or not oc or oc == t.currency or not (t.orig_amount == t.orig_amount) or t.orig_amount <= 0:
            continue
        rows = results.get(t.bank_txn_id)
        if not rows or source.get(t.bank_txn_id, "").startswith(TRUSTED):
            continue
        target = float(t.orig_amount)
        mine = [r["item_id"] for r in rows if r["item_id"]]
        if any(i not in typ.index for i in mine):
            continue
        if mine:
            if any(r["exception"] in ("RETURNED", "DUPLICATE_PAYMENT", "PARTIAL", "SHORT_DISPUTE", "OVERPAYMENT")
                   for r in rows):
                continue
            if any(len(used.get(i, ())) > 1 for i in mine):
                continue
            cur_sum = sum(float(r["amount"]) * (1 if typ.get(r["item_id"]) == "ARI" else -1) for r in rows)
            if abs(cur_sum - target) <= 0.011:
                continue
            op = {i: before.get((t.bank_txn_id, i), 0.0) for i in mine}
        elif rows[0].get("exception") != "UNIDENTIFIED":
            continue
        else:
            op = {}
        npn = norm(payer_name(t) or "")
        fe = full.get(t.entity, {})
        cids = set(fe.get(npn, ())) if npn else set()
        if not cids and len(npn) >= 12:
            for k, v in fe.items():
                if len(k) >= 12 and (k.startswith(npn) or npn.startswith(k)):
                    cids |= v
        if not cids and mine:
            cids = {items.party_id[i] for i in mine if i in items.index}
        if len(cids) != 1:
            continue
        cid = next(iter(cids))
        pool = list(byp.get(cid, ())) + mine
        for i in byp.get(cid, ()):
            op[i] = op_end[i]
        new = _fx_subset(pool, oc, target, op, typ, cur, items)
        if not new:
            continue
        if mine and set(r["item_id"] for r in new) == set(mine):
            continue
        results[t.bank_txn_id] = new
        source[t.bank_txn_id] = "exc_fix:fx"
        for i in mine:
            used.get(i, set()).discard(t.bank_txn_id)
            if not used.get(i) and items.party_id.get(i) == cid:
                byp.setdefault(cid, []).append(i)
                op_end[i] = op[i]
        for r in new:
            used.setdefault(r["item_id"], set()).add(t.bank_txn_id)
            if r["item_id"] in byp.get(cid, []):
                byp[cid].remove(r["item_id"])
        stats["R6"] = stats.get("R6", 0) + 1


def _disc_subset(cid, t, byp_all, op, typ, cur, items, max_items=60, max_states=400000):
    """R7: UNIDENTIFIED receipt of an identified payer = a UNIQUE combination of its open items (same currency) where
    each invoice is paid in full or at total - pct x total (discount terms), credit notes netted, and at least one
    invoice took the discount. Discount within booking <= issue + days + 3 -> SHORT_DISCOUNT (full amount), after it
    -> PARTIAL (amount = cash). Exact to the cent; aborts when ambiguous or too large."""
    from datetime import date, timedelta
    terms = items.attrs.get("terms") or {}
    its = [i for i in byp_all.get(cid, ()) if cur[i] == t.currency and op.get(i, 0.0) > 0.005]
    if not its or len(its) > max_items:
        return None
    target = int(round(t.amount * 100))
    opts = []
    for i in its:
        o = int(round(op[i] * 100))
        if typ[i] == "ARC":
            opts.append((i, [(-o, "")]))
            continue
        v = [(o, "")]
        pct, dd = terms.get(str(items.payment_terms[i]), (0.0, 0))
        tot = float(items.total[i])
        if pct and abs(op[i] - tot) < 0.005:
            cash = round(tot - round(tot * pct / 100.0, 2), 2)
            lim = (date.fromisoformat(str(items.issue_date[i])[:10]) + timedelta(days=dd + 3)).isoformat()
            v.append((int(round(cash * 100)), "D" if t.booking_date <= lim else "L"))
        opts.append((i, v))
    # DP: sum -> (count capped at 2, node, has_discount); node = (item, variant, parent)
    states = {(0, False): (1, None)}
    for i, vs in opts:
        new = dict(states)
        for (sm, hd), (cnt, node) in states.items():
            for val, kind in vs:
                ns = sm + val
                if ns > target or ns < -10 ** 9:
                    continue
                key = (ns, hd or kind != "")
                c0 = new.get(key)
                if c0 is None:
                    new[key] = (cnt, (i, kind, val, node))
                else:
                    new[key] = (min(2, c0[0] + cnt), c0[1])
        states = new
        if len(states) > max_states:
            return None
    hit = states.get((target, True))
    if not hit or hit[0] != 1 or states.get((target, False)):
        return None
    rows, node = [], hit[1]
    while node:
        i, kind, val, node = node
        o = round(op[i], 2)
        if kind == "L":
            rows.append({"item_id": i, "amount": round(val / 100.0, 2), "category": "", "exception": "PARTIAL"})
        elif kind == "D":
            rows.append({"item_id": i, "amount": o, "category": "", "exception": "SHORT_DISCOUNT"})
        else:
            rows.append({"item_id": i, "amount": o, "category": "", "exception": ""})
    if not any(r["exception"] for r in rows) or len(rows) > 12:
        return None
    return rows[::-1]


def _fx_eur(amount, ccy, day, items):
    """amount in ccy -> EUR at the last fx_rates fixing on/before day (None when unknown)."""
    if ccy == "EUR":
        return amount
    fx = items.attrs.get("fx") or {}
    ser = fx.get(ccy)
    if not ser:
        return None
    import bisect
    k = bisect.bisect_right(ser[0], day) - 1
    if k < 0:
        k = 0
    return amount / ser[1][k]


def _is_bank_charge(iid, ent_day, ccy, c, o, items):
    """POLICIES §3 SHORT_BANK_CHARGES on a single invoice: cross-border payer (customer country != entity country),
    shortfall <= 2 % of the item and <= 60 EUR equivalent, and not explained by a discount or withholding rate."""
    ctry = items.attrs.get("cust_country") or {}
    ecty = items.attrs.get("ent_country") or {}
    ent, day = ent_day
    pc = ctry.get(items.party_id[iid], "")
    if not pc or not ecty.get(ent) or pc == ecty.get(ent):
        return False
    short = round(o - c, 2)
    if short <= 0.005 or short > 0.02 * o + 0.005:
        return False
    eur = _fx_eur(short, ccy, day, items)
    if eur is None or eur > 60.0 + 1e-6:
        return False
    tot = float(items.total[iid])
    if abs(o - tot) < 0.005:
        for r in WHT + (0.01, 0.02, 0.03):
            if abs(short - round(tot * r, 2)) <= 0.011:
                return False
    return True


def _replaced_swap(bank, results, items, open0, repl, stats):
    """R9 (POLICIES §5.7): a row on an invoice that a PDF says was cancelled and replaced is moved to the replacement
    invoice, only when both have the same total and the replacement is not answered anywhere else."""
    if not repl:
        return
    used = {r["item_id"] for rows in results.values() for r in rows if r["item_id"]}
    tot = items.total
    for t in bank.bank_txn_id:
        rows = results.get(t)
        if not rows or not rows[0]["item_id"]:
            continue
        for r in rows:
            old = r["item_id"]
            new = repl.get(old)
            if not new or not isinstance(new, str) or new == old or new in used or new not in tot.index \
                    or old not in tot.index:
                continue
            if abs(float(tot[old]) - float(tot[new])) > 0.005 or open0.get(new, 0.0) <= 0.005:
                continue
            r["item_id"] = new
            used.add(new)
            stats["R9"] = stats.get("R9", 0) + 1


def _attach_masters(items, masters):
    cu = masters.get("customers")
    en = masters.get("entities")
    fx = masters.get("fx_rates")
    items.attrs["cust_country"] = dict(zip(cu.customer_id, cu.country)) if cu is not None else {}
    items.attrs["ent_country"] = dict(zip(en.entity, en.country)) if en is not None else {}
    d = {}
    if fx is not None:
        f = fx.sort_values(["currency", "date"])
        for c, g in f.groupby("currency"):
            d[c] = (list(g.date), [float(x) for x in g.units_per_eur])
    items.attrs["fx"] = d


def _terms(pt):
    out = {}
    if pt is None:
        return out
    for r in pt.itertuples():
        try:
            if r.early_payment_discount_pct and r.discount_days:
                out[r.code] = (float(r.early_payment_discount_pct), int(float(r.discount_days)))
        except ValueError:
            pass
    return out


def run(ctx):
    try:
        import pdfinfo as P
        pdf = {"ret": P.retention(ctx), "cnmode": P.cn_mode(ctx), "rappel": P.rappel_mode(ctx),
               "repl": P.replaced_by(ctx) if ENABLE["R9"] else {}}
    except Exception as ex:  # noqa: BLE001
        print(f"    exc_fix: no pdf info ({ex})")
        pdf = {}
    if ENABLE["R5"]:
        ctx.items.attrs["customers"] = ctx.masters.get("customers")
        ctx.items.attrs["terms"] = _terms(ctx.masters.get("payment_terms"))
    if ENABLE["R8"]:
        _attach_masters(ctx.items, ctx.masters)
    stats = rewrite(ctx.bank, ctx.results, ctx.source, ctx.items, opening(ctx), pdf)
    print(f"    exc_fix: {stats}")
