"""AR receipts netting return credit notes (POLICIES §2, §5.9) -- runs BEFORE v3import.

Rule (no labels used): a customer pays a set S of invoices minus the open return credit notes that reference them
(credit_notes.related_document = invoice number). The older model (v3) often sees such a line as "invoice X paid
PARTIAL" (cash = X - CN). When
    cash == sum(open amount of S) - sum(open amount of the credit notes C related to S)     (to the cent, same currency)
the line clears every invoice of S in full and every credit note of C with its full positive amount, no exception.

Candidate sets S: the invoices v3 proposed for the line, plus invoices whose number is quoted in the bank text.
Credit notes considered: open ARC of the same customer and entity, reason 'Devolución / rectificación' (cancellation
credit notes are never settled by bank; rappel credit notes only when their PDF says 'offset'), plus a credit note
whose number is quoted in the bank text ("CN 400603"). Only claims when exactly one subset of C closes the gap.
"""
import os
import re
import sys
from collections import defaultdict
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import full_text, norm  # noqa: E402

TOKEN_RE = re.compile(r"[A-Z0-9][A-Z0-9\-/]{3,}")


def cents(x):
    return int(round(float(x) * 100))


def _v3(ctx):
    try:
        from passes import v3import
        return v3import._load(ctx)
    except SystemExit:
        return {}


def _index(ctx):
    it = ctx.items
    ar = it[it.item_type.isin(["ARI", "ARC"])]
    try:
        import pdfinfo as P
        rappel = P.rappel_mode(ctx) or {}
    except Exception:
        rappel = {}
    rel = defaultdict(list)       # (entity, invoice doc_number) -> [ARC ids]
    doc = {}                      # (entity, norm doc) -> item id
    digits = defaultdict(set)     # (entity, digits w/o leading zeros) -> item ids (len >= 6)
    for iid, typ, ent, dn, rd, rsn in zip(ar.item_id, ar.item_type.astype(str), ar.entity, ar.doc_number,
                                          ar.related_document, ar.reason.astype(str)):
        nd = norm(dn)
        if nd:
            doc[(ent, nd)] = iid
        dg = re.sub(r"\D", "", dn or "").lstrip("0")
        if len(dg) >= 6:
            digits[(ent, dg)].add(iid)
        if typ == "ARC" and rd:
            ok = rsn.startswith("Devol") or (rsn.startswith("Rappel") and rappel.get(iid) == "offset")
            if ok:
                rel[(ent, rd)].append(iid)
    return rel, doc, digits


def _text_items(t, ent, doc, digits):
    """Items whose number is quoted in the bank text (full number, or digits of >= 6 chars)."""
    s = full_text(t._asdict())
    out = set()
    for tok in TOKEN_RE.findall(s):
        n = norm(tok)
        if (ent, n) in doc:
            out.add(doc[(ent, n)])
    for dg in re.findall(r"\d{6,}", s):
        dg = dg.lstrip("0")
        if len(digits.get((ent, dg), ())) == 1:
            out |= digits[(ent, dg)]
    return out


def run(ctx):
    v3 = _v3(ctx)
    rel, doc, digits = _index(ctx)
    items = ctx.items
    typ = items.item_type.astype(str).to_dict()
    ent_of = items.entity.astype(str).to_dict()
    pid_of = items.party_id.to_dict()
    cur_of = items.currency.astype(str).to_dict()
    dn_of = items.doc_number.to_dict()
    isd_of = items.issue_date.to_dict()
    stats = defaultdict(int)
    pend = ctx.pending()
    pend = pend[pend.amount > 0]
    try:
        import pdfinfo as P
        special = set(P.instalments(ctx) or {}) | set(P.retention(ctx) or {}) | set(P.advance(ctx) or {})
    except Exception:
        special = set()
    ar = ctx.items[ctx.items.item_type.isin(["ARI", "ARC"])]
    pidx = defaultdict(list)
    for i, p in zip(ar.item_id, ar.party_id):
        if i in ctx.open:
            pidx[p].append(i)
    for t in pend.itertuples(index=False):
        rows = v3.get(t.bank_txn_id)
        if not rows or not rows[0]["item_id"]:
            continue
        V = [r["item_id"] for r in rows]
        if any(typ.get(i) not in ("ARI", "ARC") for i in V):
            continue
        excs = {r["exception"] for r in rows}
        if not excs <= {"PARTIAL", "SHORT_DISPUTE", ""}:
            continue
        ent = t.entity
        pids = {pid_of.get(i) for i in V}
        curs = {cur_of.get(i) for i in V}
        if len(pids) != 1 or curs != {t.currency} or any(ent_of.get(i) != ent for i in V):
            continue
        pid = next(iter(pids))
        if any(i not in ctx.open for i in V) or any(i in special for i in V):
            continue
        S = [i for i in V if typ[i] == "ARI"]
        txt = full_text(t._asdict())
        runs = {r.lstrip("0") for r in re.findall(r"\d{5,}", txt)}
        runs = {r for r in runs if len(r) >= 5}
        sfx = set()
        if runs:
            for i in pidx.get(pid, ()):
                if i in ctx.open and ent_of.get(i) == ent:
                    dg = re.sub(r"\D", "", dn_of.get(i) or "")
                    if any(dg.endswith(r) for r in runs):
                        sfx.add(i)
        quoted = {i for i in (_text_items(t, ent, doc, digits) | sfx) if pid_of.get(i) == pid and i in ctx.open
                  and cur_of.get(i) == t.currency and isd_of.get(i, "9") <= t.booking_date and i not in special}
        q_inv = sorted(i for i in quoted if typ.get(i) == "ARI" and i not in S)[:4]
        A = cents(t.amount)
        found = []
        for k in range(len(q_inv) + 1):
            for extra in combinations(q_inv, k):
                SS = S + list(extra)
                if not SS:
                    continue
                base = sum(cents(ctx.open[i]) for i in SS)
                docs = {dn_of.get(i) for i in SS}
                cands = {i for i in V if typ[i] == "ARC"} | {i for i in quoted if typ.get(i) == "ARC"}
                for i in SS:
                    for c in rel.get((ent, dn_of.get(i)), ()):
                        if c in ctx.open and pid_of.get(c) == pid and cur_of.get(c) == t.currency \
                                and isd_of.get(c, "9") <= t.booking_date:
                            cands.add(c)
                cands = sorted(c for c in cands if c in quoted or items.at[c, "related_document"] in docs)
                if len(cands) > 10:
                    continue
                for kk in range(0, min(len(cands), 4) + 1):
                    for C in combinations(cands, kk):
                        if base - sum(cents(ctx.open[c]) for c in C) == A:
                            sc = sum(1 for i in list(SS) + list(C) if i in quoted)
                            found.append((sc, SS, C))
        if not found:
            stats["nomatch"] += 1
            continue
        best = max(f[0] for f in found)
        top = [f for f in found if f[0] == best]
        if len(top) != 1:
            stats["ambig"] += 1
            continue
        _, SS, C = top[0]
        if not C or (set(SS) | set(C)) == set(V) and excs == {""}:
            stats["same"] += 1
            continue
        out = [{"item_id": i, "amount": ctx.open[i], "exception": ""} for i in SS]
        out += [{"item_id": c, "amount": ctx.open[c], "exception": ""} for c in C]
        ctx.allocate(t.bank_txn_id, out, "multi:cn_net")
        stats["cn_net"] += 1
    print("    ar_multi:", dict(stats))
