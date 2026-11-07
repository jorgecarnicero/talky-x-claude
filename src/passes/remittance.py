"""Remittance-advice pass.

Reads the offline cache docs_ra_<phase>.parquet (src/docs_extract.py) for every phase folder in ctx.side_dirs, matches
each advice to its bank credit (entity + amount [or instructed amount / FX-converted amount] + payment reference in the
bank text, or payer name when the reference is missing; the advice may arrive days after the cash) and allocates the
listed documents following POLICIES:
  * credit notes listed -> full open amount, no exception
  * deduction with a stated dispute reason -> SHORT_DISPUTE, amount = paid
  * deduction 'DISCOUNT' -> SHORT_DISCOUNT (full) when within terms (+3 days grace), else PARTIAL (paid)
  * deduction 'RET' (retención / withholding) -> SHORT_WITHHOLDING (full)
  * advice lines sum > cash: explained by withholding rate, early-payment discount, or the last line is PARTIAL
  * bank currency != item currency -> FX_DIFFERENCE on fully cleared invoice rows
Only claims a bank line when every advice line resolves to an item and the amounts reconcile.
"""
import os
import re
import sys
from collections import defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import CACHE, full_text, norm, norm_words  # noqa: E402

DISPUTE_WORDS = re.compile(r"PRICE|DAMAG|SHORT|DELIVER|PENAL|RETRAS|COOP|MARKET|MKT|LOGIST|FEE|QUALITY|DEFECT|"
                           r"MISSING|RETURN|CLAIM|DISPUT|DIFF|PROMO|REBATE|LATE|DIFEREN|DANO|DAÑ|ABZUG|MANGEL|"
                           r"RECLAM|LITIGE|AVARIA|FALT", re.I)
DISCOUNT_WORDS = re.compile(r"DISCOUNT|DESCUENTO|SKONTO|ESCOMPTE|SCONTO|DESCONTO|PRONTO PAGO|DTO", re.I)
WHT_WORDS = re.compile(r"^RET\b|RETENC|WITHHOLD|WHT|ISR|IRRF|RETENUE|RAS\b|QUELLENST", re.I)
WHT_RATES = (0.0125, 0.015, 0.10)
WHT_COUNTRIES = {"MX", "BR", "MA"}      # POLICIES §3: withholding at source by some customers there
DISPUTE_RATIOS = (0.03, 0.05, 0.10, 0.20, 0.30)
LEGAL = {"SA", "SL", "SLU", "SAS", "SARL", "GMBH", "AG", "KG", "CO", "LTD", "LLC", "INC", "CORP", "BV", "SPA", "SRL",
         "LDA", "EIRELI", "LTDA", "PTY", "DE", "CV", "Z", "OO", "SP", "THE", "Y", "E", "AND", "GROUP", "S", "A", "C",
         "SCOOP", "COOP", "HERMANOS", "HIJOS", "NV", "PLC", "LIMITED", "COMPANY", "ET", "CIE", "DO", "DA"}


def _load(ctx):
    frames = []
    for d in ctx.side_dirs:
        ph = os.path.basename(os.path.normpath(d))
        p = os.path.join(CACHE, f"docs_ra_{ph}.parquet")
        if os.path.exists(p):
            frames.append(pd.read_parquet(p))
        else:
            print(f"    remittance: no cache {p} (run src/docs_extract.py --phase {ph})")
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def _name_tokens(s):
    return {w for w in norm_words(s).split() if len(w) > 2 and w not in LEGAL}


def _fx_rate_fn(ctx):
    fx = ctx.masters.get("fx_rates")
    if fx is None or not len(fx):
        return lambda cur, date: None
    fx = fx.copy()
    fx["units_per_eur"] = pd.to_numeric(fx["units_per_eur"], errors="coerce")
    series = {c: g.sort_values("date")[["date", "units_per_eur"]].values for c, g in fx.groupby("currency")}

    def rate(cur, date):
        if cur == "EUR":
            return 1.0
        s = series.get(cur)
        if s is None:
            return None
        import bisect
        i = bisect.bisect_right([r[0] for r in s], date) - 1
        return float(s[max(i, 0)][1])
    return rate


def run(ctx):
    ra = _load(ctx)
    if ra is None or not len(ra):
        return
    bank = ctx.pending()
    if not len(bank):
        return
    dmin, dmax = bank.booking_date.min(), bank.booking_date.max()
    lo = str(pd.Timestamp(dmin) - pd.Timedelta(days=5))[:10]
    hi = str(pd.Timestamp(dmax) + pd.Timedelta(days=30))[:10]
    if getattr(ctx, "horizon", None):  # dev window: no advice received after the window end
        hi = min(hi, ctx.horizon)
    ra = ra[(ra.received_date >= lo) & (ra.received_date <= hi) & (ra.n_lines > 0)]
    if not len(ra):
        return

    # ------------------------------------------------------------- bank credits index
    cred = bank[bank.amount > 0]
    by_amt = defaultdict(list)
    by_ent = defaultdict(list)
    rows = {}
    for t in cred.to_dict("records"):
        t["_ft"] = full_text(t)
        t["_ftn"] = norm(t["_ft"])
        rows[t["bank_txn_id"]] = t
        by_amt[(t["entity"], round(t["amount"], 2))].append(t)
        oa = t.get("orig_amount")
        if oa == oa and oa is not None:
            by_amt[(t["entity"], round(float(oa), 2))].append(t)
        by_ent[t["entity"]].append(t)
    rate = _fx_rate_fn(ctx)

    # ------------------------------------------------------------- item indexes (AR side)
    it = ctx.items
    ar = it[it.item_type.isin(["ARI", "ARC"])]
    doc_idx = defaultdict(list)     # (entity, norm doc) -> item ids
    po_idx = defaultdict(list)      # (entity, norm po) -> ARI ids
    for iid, ent, doc, po in zip(ar.item_id, ar.entity, ar.doc_number, ar.po):
        doc_idx[(ent, norm(doc))].append(iid)
        if isinstance(po, str) and po:
            po_idx[(ent, norm(po))].append(iid)
    party_items = defaultdict(list)
    for iid, pid in zip(ar.item_id, ar.party_id):
        party_items[pid].append(iid)
    terms = {}
    pt = ctx.masters.get("payment_terms")
    if pt is not None:
        for r in pt.itertuples():
            try:
                pct = float(r.early_payment_discount_pct) if r.early_payment_discount_pct else 0.0
                dd = int(float(r.discount_days)) if r.discount_days else 0
            except ValueError:
                pct, dd = 0.0, 0
            terms[r.code] = (pct, dd)
    cust = ctx.masters.get("customers")
    cust_names = defaultdict(set)
    cust_iban = defaultdict(set)
    if cust is not None:
        for r in cust.itertuples():
            cust_names[r.customer_id] |= _name_tokens(r.name) | _name_tokens(r.trade_name)
            if r.iban:
                cust_iban[r.customer_id].add(norm(r.iban))
        ctx.cache["_cust_country"] = dict(zip(cust.customer_id, cust.country))

    suf_cache = {}

    def suffix_index(pid):
        """norm-doc suffixes (>=4 chars, also digits-only without leading zeros) -> open item ids of the party."""
        d = suf_cache.get(pid)
        if d is None:
            d = defaultdict(set)
            for iid in party_items.get(pid, ()):
                if iid not in ctx.open:
                    continue
                dn = norm(it.at[iid, "doc_number"])
                dd = re.sub(r"\D", "", dn).lstrip("0")
                for x in (dn, dd):
                    for i in range(0, max(0, len(x) - 3)):
                        d[x[i:]].add(iid)
            suf_cache[pid] = d
        return d

    def resolve(ent, ln, parties):
        """Advice line -> item id (or None)."""
        doc, po, vch = ln["doc"], ln["po"], ln["voucher"]
        for tok in (doc, vch):
            k = norm(tok)
            if not k:
                continue
            c = doc_idx.get((ent, k))
            if c:
                return _pick(c, parties)
        # suffix / no-prefix / no-leading-zero match within the payer's open items
        for tok in (doc, vch):
            k = norm(tok)
            if len(k) < 4 or not parties:
                continue
            cands = set()
            for pid in parties:
                cands |= suffix_index(pid).get(k, set()) | suffix_index(pid).get(k.lstrip("0"), set())
            cands = sorted(cands)
            if len(cands) == 1:
                return cands[0]
            if len(cands) > 1:
                g = [c for c in cands if abs(float(it.at[c, "total"]) - abs(ln["gross"] or 0)) < 0.02]
                if len(g) == 1:
                    return g[0]
        # very short numbers (credit notes in voucher layouts): digits suffix + same amount, payer's open items
        k = norm(doc)
        if 2 <= len(k) < 4 and k.isdigit() and parties and ln["gross"] == ln["gross"]:
            want = "ARC" if (ln["gross"] or 0) < 0 else "ARI"
            cands = [iid for pid in parties for iid in party_items.get(pid, ())
                     if iid in ctx.open and it.at[iid, "item_type"] == want
                     and re.sub(r"\D", "", norm(it.at[iid, "doc_number"])).endswith(k)
                     and abs(float(it.at[iid, "total"]) - abs(ln["gross"])) < 0.02]
            if len(cands) == 1:
                return cands[0]
        k = norm(po)
        if k:
            c = po_idx.get((ent, k))
            if c:
                c2 = [x for x in c if abs(float(it.at[x, "total"]) - abs(ln["gross"] or 0)) < 0.02] or c
                if len(c2) == 1:
                    return c2[0]
        return None

    def _pick(c, parties):
        if len(c) == 1:
            return c[0]
        if parties:
            c2 = [x for x in c if it.at[x, "party_id"] in parties]
            if len(c2) == 1:
                return c2[0]
            c = c2 or c
        c2 = [x for x in c if x in ctx.open]
        return c2[0] if c2 else c[-1]

    # ------------------------------------------------------------- per-advice processing
    used = set()
    stats = defaultdict(int)
    dbg = []
    adv = []
    for f, g in ra.groupby("file", sort=False):
        h = g.iloc[0]
        adv.append((h.received_date, f, h, g.sort_values("line_no")))
    adv.sort(key=lambda a: (a[0], a[1]))

    # payer -> customers learnt from exact doc matches (helps the no-prefix layouts)
    payer_parties = defaultdict(set)
    for _, f, h, g in adv:
        for ln in g.itertuples():
            c = doc_idx.get((h.entity, norm(ln.doc)))
            if c and len(c) == 1:
                payer_parties[(h.entity, h.payer_name)].add(it.at[c[0], "party_id"])

    for rdate, f, h, g in adv:
        ent = h.entity
        total = h.total
        if total != total or total is None:
            stats["no_total"] += 1
            continue
        lines = [dict(doc=r.doc, po=r.po, voucher=r.voucher, gross=r.gross, paid=r.paid, deduction=r.deduction,
                      reason=r.reason, doc_date=r.doc_date) for r in g.itertuples()]
        parties = set(payer_parties.get((ent, h.payer_name), set()))
        ids = []
        for ln in lines:
            iid = resolve(ent, ln, parties)
            ids.append(iid)
            if iid is not None:
                parties.add(it.at[iid, "party_id"])
        nbad = sum(i is None for i in ids)
        if nbad and (nbad > max(1, 0.02 * len(ids)) or len(ids) < 20):
            stats["unresolved_doc"] += 1
            bad = [ln for ln, i in zip(lines, ids) if i is None]
            dbg.append((f, "unresolved_doc", f"{len(bad)}/{len(lines)} e.g. {bad[0]['doc']}|{bad[0]['po']}|{bad[0]['gross']}"))
            continue
        rid = [i for i in ids if i is not None]
        if len(set(rid)) != len(rid):
            stats["dup_items"] += 1
            continue
        item_cur = it.at[rid[0], "currency"]

        # ---- find the bank line
        ref = norm(h.payment_ref)
        anchor = h.pay_date if isinstance(h.pay_date, str) and h.pay_date else rdate
        d_lo = str(pd.Timestamp(min(anchor, rdate)) - pd.Timedelta(days=10))[:10]
        d_hi = str(pd.Timestamp(max(anchor, rdate)) + pd.Timedelta(days=5))[:10]
        cands = [t for t in by_amt.get((ent, round(total, 2)), []) if d_lo <= t["booking_date"] <= d_hi]
        fx_line = False
        if not cands:
            # FX: bank in another currency, no instructed amount -> convert with reference rates (3 % tolerance)
            for t in by_ent.get(ent, []):
                if t["currency"] == item_cur or not (d_lo <= t["booking_date"] <= d_hi):
                    continue
                if ref and len(ref) >= 6 and ref in t["_ftn"]:
                    r1, r2 = rate(item_cur, t["booking_date"]), rate(t["currency"], t["booking_date"])
                    if r1 and r2:
                        conv = total / r1 * r2
                        if abs(t["amount"] - conv) <= 0.03 * conv:
                            cands.append(t)
        cands = [t for t in cands if t["bank_txn_id"] not in used and not ctx.done(t["bank_txn_id"])]
        if not cands:
            stats["no_bank"] += 1
            dbg.append((f, "no_bank", ""))
            continue
        refc = [t for t in cands if ref and len(ref) >= 6 and ref in t["_ftn"]]
        if len(refc) == 1:
            t = refc[0]
        elif len(refc) > 1:
            refc.sort(key=lambda t: abs((pd.Timestamp(t["booking_date"]) - pd.Timestamp(anchor)).days))
            t = refc[0]
        else:
            # no reference in the bank text: payer name / IBAN must match and be unique
            ntok = _name_tokens(h.payer_name)
            for pid in parties:
                ntok |= cust_names.get(pid, set())
            ibans = set().union(*[cust_iban.get(p, set()) for p in parties]) if parties else set()

            def name_ok(t):
                words = set(norm_words(t["_ft"]).split())
                if ibans and any(ib and ib in t["_ftn"] for ib in ibans):
                    return True
                return len(ntok & words) >= min(2, len(ntok)) and len(ntok) > 0
            nc = [t for t in cands if name_ok(t)]
            if len(nc) != 1:
                stats["ambiguous_bank"] += 1
                dbg.append((f, "ambiguous_bank", ",".join(x["bank_txn_id"] for x in nc)))
                continue
            t = nc[0]
        fx_line = t["currency"] != item_cur
        alloc = _build_rows(ctx, t, lines, ids, total, terms, fx_line, stats)
        if alloc is None:
            dbg.append((f, "rows_none", t["bank_txn_id"]))
            continue
        used.add(t["bank_txn_id"])
        ctx.allocate(t["bank_txn_id"], alloc, "remittance")
        stats["claimed"] += 1
    print("    remittance:", dict(stats))
    if os.environ.get("REMIT_DEBUG"):
        pd.DataFrame(dbg, columns=["file", "why", "info"]).to_csv(os.environ["REMIT_DEBUG"], index=False)


def _build_rows(ctx, t, lines, ids, total, terms, fx_line, stats):
    it = ctx.items
    country = ctx.cache.get("_cust_country", {})

    def wht_party(iid):
        return country.get(it.at[iid, "party_id"], "") in WHT_COUNTRIES
    book = t["booking_date"]
    out = []
    S = round(sum((ln["paid"] if ln["paid"] == ln["paid"] else 0.0) for ln in lines), 2)
    diff = round(S - total, 2)
    tol = max(0.02, 0.0005 * abs(total))
    keep = [(ln, iid) for ln, iid in zip(lines, ids) if iid is not None]   # unresolved lines: cash counted, no row
    if len(keep) < len(lines) and abs(diff) > tol:
        stats["unresolved_gap"] += 1
        return None
    lines, ids = [k[0] for k in keep], [k[1] for k in keep]

    def openamt(iid):
        o = ctx.open.get(iid)
        return float(o) if o is not None else 0.0

    def disc_ok(iid, cash):
        """Early-payment discount within terms: returns (ok_in_time, expected_cash) or None if no discount terms."""
        code = it.at[iid, "payment_terms"]
        pct, days = terms.get(code, (0.0, 0))
        if not pct:
            return None
        tot = float(it.at[iid, "total"])
        exp = round(tot - round(tot * pct / 100.0, 2), 2)
        lim = str(pd.Timestamp(it.at[iid, "issue_date"]) + pd.Timedelta(days=days + 3))[:10]
        return (book <= lim, exp)

    # ---- line-level decisions when the advice reconciles with the cash
    if abs(diff) <= tol:
        for ln, iid in zip(lines, ids):
            typ = it.at[iid, "item_type"]
            o = openamt(iid)
            paid = abs(ln["paid"])
            ded = ln["deduction"] if ln["deduction"] == ln["deduction"] else 0.0
            if abs(ded) <= 0.005 and ln["gross"] == ln["gross"]:
                ded = round(paid - abs(ln["gross"]), 2) if typ != "ARC" else 0.0
            reason = (ln["reason"] or "").strip()
            if typ == "ARC":
                out.append({"item_id": iid, "amount": o or paid, "exception": ""})
                continue
            if o <= 0.005:
                stats["closed_item"] += 1
                return None
            tot = float(it.at[iid, "total"])
            full_exc = "FX_DIFFERENCE" if fx_line else ""
            if abs(ded) <= 0.005:
                if abs(paid - o) <= max(0.02, 0.005 * o):
                    out.append({"item_id": iid, "amount": o, "exception": full_exc})
                elif paid < o:
                    out.append({"item_id": iid, "amount": paid, "exception": "PARTIAL"})
                else:
                    out.append({"item_id": iid, "amount": o, "exception": "OVERPAYMENT"})
                continue
            dabs = abs(ded)
            is_wht = WHT_WORDS.search(reason) if reason else (
                wht_party(iid) and any(abs(dabs - round(tot * r, 2)) <= 0.02 for r in WHT_RATES))
            if is_wht:
                out.append({"item_id": iid, "amount": o, "exception": "FX_DIFFERENCE" if fx_line else "SHORT_WITHHOLDING"})
                continue
            d = disc_ok(iid, paid)
            is_disc = DISCOUNT_WORDS.search(reason) if reason else (d is not None and abs(d[1] - paid) <= 0.02)
            if is_disc:
                if d is not None and d[0] and abs(d[1] - paid) <= 0.02 and abs(o - tot) < 0.02:
                    out.append({"item_id": iid, "amount": o, "exception": "FX_DIFFERENCE" if fx_line else "SHORT_DISCOUNT"})
                else:
                    out.append({"item_id": iid, "amount": paid, "exception": "PARTIAL"})
                continue
            if reason:
                out.append({"item_id": iid, "amount": paid, "exception": "SHORT_DISPUTE"})
                continue
            # deduction without a stated reason: a typical dispute-sized cut (layouts without a reason column)
            base = abs(ln["gross"]) if ln["gross"] == ln["gross"] and ln["gross"] else tot
            ratio = dabs / base if base else 0
            if any(abs(ratio - r) < 0.0006 for r in DISPUTE_RATIOS):
                out.append({"item_id": iid, "amount": paid, "exception": "SHORT_DISPUTE"})
            else:
                out.append({"item_id": iid, "amount": paid, "exception": "PARTIAL"})
        return out

    if diff < 0:
        stats["cash_gt_advice"] += 1
        return None
    # ---- cash < advice: explain the gap
    inv = [(ln, iid) for ln, iid in zip(lines, ids) if it.at[iid, "item_type"] == "ARI"]
    if any(openamt(iid) <= 0.005 for _, iid in inv):
        stats["closed_item"] += 1
        return None
    # (a) withholding at one rate on every invoice
    for r in WHT_RATES:
        w = round(sum(round(float(it.at[iid, "total"]) * r, 2) for _, iid in inv), 2)
        if abs(w - diff) <= 0.02 * max(1, len(inv)) * 0.5 + 0.01:
            for ln, iid in zip(lines, ids):
                typ = it.at[iid, "item_type"]
                exc = "" if typ == "ARC" else ("FX_DIFFERENCE" if fx_line else "SHORT_WITHHOLDING")
                out.append({"item_id": iid, "amount": openamt(iid) or abs(ln["paid"]), "exception": exc})
            stats["wht"] += 1
            return out
    # (b) early-payment discount taken on a subset of the invoices with discount terms
    disc = {}
    for ln, iid in inv:
        d = disc_ok(iid, None)
        if d is not None:
            disc[iid] = (d[0], round(float(it.at[iid, "total"]) - d[1], 2))
    sub = _subset_sum(list(disc.items()), diff) if disc else None
    if sub is not None:
        for ln, iid in zip(lines, ids):
            typ = it.at[iid, "item_type"]
            o = openamt(iid) or abs(ln["paid"])
            if iid in sub:
                if disc[iid][0]:
                    out.append({"item_id": iid, "amount": o, "exception": "FX_DIFFERENCE" if fx_line else "SHORT_DISCOUNT"})
                else:
                    out.append({"item_id": iid, "amount": round(o - disc[iid][1], 2), "exception": "PARTIAL"})
            else:
                out.append({"item_id": iid, "amount": o, "exception": "FX_DIFFERENCE" if (fx_line and typ == "ARI") else ""})
        stats["disc"] += 1
        return out
    # (c) cash ran out: lines paid in order, the last invoice line is partially paid
    if fx_line:
        stats["fx_gap"] += 1
        return None
    last = max(i for i, (ln, iid) in enumerate(zip(lines, ids)) if it.at[iid, "item_type"] == "ARI")
    for i, (ln, iid) in enumerate(zip(lines, ids)):
        o = openamt(iid) or abs(ln["paid"])
        if i == last:
            amt = round(abs(ln["paid"]) - diff, 2)
            if amt <= 0.005:
                stats["neg_partial"] += 1
                return None
            out.append({"item_id": iid, "amount": amt, "exception": "PARTIAL"})
        else:
            out.append({"item_id": iid, "amount": o, "exception": ""})
    stats["n_to_m"] += 1
    return out


def _subset_sum(cands, target, max_k=4):
    """cands: [(iid, (in_time, disc_amount))]. Smallest subset whose discount amounts sum to target (+-0.02)."""
    from itertools import combinations
    cands = cands[:18]
    for k in range(1, min(max_k, len(cands)) + 1):
        hits = [c for c in combinations(cands, k) if abs(sum(x[1][1] for x in c) - target) <= 0.02]
        if len(hits) == 1:
            return {x[0] for x in hits[0]}
        if len(hits) > 1:
            return None
    return None
