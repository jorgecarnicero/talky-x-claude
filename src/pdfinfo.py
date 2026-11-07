"""Clauses parsed from document PDFs, keyed by item_id (for the AR/AP matchers).

Built offline by `src/docs_extract.py --phase <p>` into ../cache/docs_pdf_<p>.parquet; here we only read the caches of
every phase folder in ctx.side_dirs and link each PDF to its item (entity + item type + document number [+ party]).

    from pdfinfo import load_pdf_info, instalments, retention, advance, replaced_by, service_month, \
        rappel_mode, cn_mode, factored, new_ibans
    info = load_pdf_info(ctx)          # DataFrame, one row per PDF, column item_id ("" when not an item)

All helpers cache their result in ctx.cache and return {} / empty frames when no cache exists.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import CACHE, norm  # noqa: E402

TYPE_MAP = {"invoice": "ARI", "credit_note": "ARC", "supplier_invoice": "API", "supplier_credit_note": "APC"}
COLS = ["file", "document_type", "document_number", "entity", "party_id", "document_date", "doc_total", "doc_currency",
        "n_instalments", "instalment_days", "instalment_amounts", "retention_pct", "retention_amount",
        "retention_release_date", "amount_payable", "advance_proforma", "advance_amount", "net_due",
        "replaces_document", "replaces_date", "cancels_document", "service_month", "rappel_period", "rappel_mode",
        "cn_mode", "factor_name", "new_iban", "old_iban", "iban_effective_date", "clause_text"]


def load_pdf_info(ctx, with_text=False):
    key = "pdfinfo_df"
    if key in ctx.cache:
        return ctx.cache[key]
    frames = []
    for d in ctx.side_dirs:
        ph = os.path.basename(os.path.normpath(d))
        p = os.path.join(CACHE, f"docs_pdf_{ph}.parquet")
        if not os.path.exists(p):
            print(f"    pdfinfo: no cache {p} (run src/docs_extract.py --phase {ph} --only pdf)")
            continue
        df = pd.read_parquet(p)
        keep = [c for c in COLS if c in df.columns] + (["text"] if with_text and "text" in df.columns else [])
        frames.append(df[keep])
    if not frames:
        out = pd.DataFrame(columns=COLS + ["item_type", "item_id"])
        ctx.cache[key] = out
        return out
    info = pd.concat(frames, ignore_index=True).drop_duplicates("file", keep="last").reset_index(drop=True)
    hz = getattr(ctx, "horizon", None)
    if hz:  # dev window: no document dated after the window end (IBAN notices, replacements, instalment plans)
        dd = info.document_date.fillna("").astype(str)
        info = info[(dd == "") | (dd <= hz)].reset_index(drop=True)
    info["item_type"] = info.document_type.map(TYPE_MAP).fillna("")
    # link to items: (entity, type, norm(doc)) -> item id; disambiguate by party (vectorised merge)
    it = ctx.items
    info["_k"] = info.document_number.map(norm)
    need = set(info._k)
    sub = it[it.doc_number.isin(set(info.document_number)) | it.doc_number.str.upper().str.replace(
        r"[^A-Z0-9]", "", regex=True).isin(need)]
    cand = pd.DataFrame({"item_id": sub.item_id.values, "item_type": sub.item_type.astype(str).values,
                         "entity": sub.entity.astype(str).values, "_k": sub.doc_number.map(norm).values,
                         "_pid": sub.party_id.values})
    m = info.reset_index().merge(cand, on=["entity", "item_type", "_k"], how="left")
    m["_same"] = (m._pid == m.party_id)
    m = m.sort_values(["index", "_same"], ascending=[True, False]).drop_duplicates("index")
    ids = m.set_index("index").item_id.reindex(range(len(info))).fillna("").tolist()
    info = info.reset_index(drop=True)
    info["item_id"] = ids
    info = info.drop(columns=["_k"])
    ctx.cache[key] = info
    return info


def _dict(ctx, name, mask_fn, val_fn):
    k = "pdfinfo_" + name
    if k not in ctx.cache:
        info = load_pdf_info(ctx)
        sub = info[(info.item_id != "") & mask_fn(info)] if len(info) else info
        ctx.cache[k] = {r.item_id: val_fn(r) for r in sub.itertuples()}
    return ctx.cache[k]


def _f(x):
    return None if x is None or x != x else float(x)


def instalments(ctx):
    """item_id -> {'n': 3, 'days': [60, 90, 120], 'amounts': [..]} (ARI whose PDF says it is paid in instalments)."""
    return _dict(ctx, "inst", lambda d: d.n_instalments.notna(), lambda r: {
        "n": int(r.n_instalments),
        "days": [int(x) for x in str(r.instalment_days).split("/") if x.isdigit()],
        "amounts": [float(x) for x in str(r.instalment_amounts).split(";") if x]})


def retention(ctx):
    """item_id -> {'pct', 'amount' (retained), 'release_date', 'payable' (amount due at due date)}."""
    return _dict(ctx, "ret", lambda d: d.retention_pct.notna(), lambda r: {
        "pct": _f(r.retention_pct), "amount": _f(r.retention_amount), "release_date": r.retention_release_date,
        "payable": _f(r.amount_payable)})


def advance(ctx):
    """item_id -> {'proforma', 'amount' (advance to deduct), 'net_due'}."""
    return _dict(ctx, "adv", lambda d: d.advance_amount.notna(), lambda r: {
        "proforma": r.advance_proforma, "amount": _f(r.advance_amount), "net_due": _f(r.net_due)})


def service_month(ctx):
    """item_id -> 'YYYY-MM' service month of monthly fee invoices."""
    return _dict(ctx, "month", lambda d: d.service_month.fillna("") != "", lambda r: r.service_month)


def rappel_mode(ctx):
    """ARC item_id -> 'offset' | 'paid_out' (volume rebate credit notes)."""
    return _dict(ctx, "rappel", lambda d: d.rappel_mode.fillna("") != "", lambda r: r.rappel_mode)


def cn_mode(ctx):
    """ARC/APC item_id -> 'offset' | 'refund' (credit note offset in next payment vs refunded by transfer)."""
    return _dict(ctx, "cnmode", lambda d: d.cn_mode.fillna("") != "", lambda r: r.cn_mode)


def factored(ctx):
    """API item_id -> factor / confirming entity name the receivable was assigned to."""
    return _dict(ctx, "factor", lambda d: d.factor_name.fillna("") != "", lambda r: r.factor_name)


def cancels(ctx):
    """ARC item_id -> document number of the invoice it cancels (from the credit-note PDF)."""
    return _dict(ctx, "cancels", lambda d: d.cancels_document.fillna("") != "", lambda r: r.cancels_document)


def replaced_by(ctx):
    """Cancelled/replaced invoices: {old_item_id: new_item_id} plus {(entity, norm(old_doc)): new_item_id}.
    A payment quoting the old number settles the replacement (POLICIES §5.7)."""
    k = "pdfinfo_repl"
    if k in ctx.cache:
        return ctx.cache[k]
    info = load_pdf_info(ctx)
    out = {}
    if len(info):
        sub = info[(info.item_id != "") & (info.replaces_document.fillna("") != "")]
        it = ctx.items
        ar = it[it.item_type == "ARI"]
        didx = {}
        for iid, ent, doc in zip(ar.item_id, ar.entity, ar.doc_number):
            didx.setdefault((ent, norm(doc)), iid)
        for r in sub.itertuples():
            kk = (r.entity, norm(r.replaces_document))
            out[kk] = r.item_id
            old = didx.get(kk)
            if old:
                out[old] = r.item_id
    ctx.cache[k] = out
    return out


def new_ibans(ctx):
    """DataFrame of bank-account change notices / supplier IBAN changes:
    entity, party_id, document_type, new_iban, old_iban, effective_date. Also ctx.cache['pdfinfo_iban_party'] =
    {norm(iban): party_id} for both new and old IBANs (both stay valid)."""
    k = "pdfinfo_iban"
    if k in ctx.cache:
        return ctx.cache[k]
    info = load_pdf_info(ctx)
    if not len(info):
        df = pd.DataFrame(columns=["entity", "party_id", "document_type", "new_iban", "old_iban", "effective_date"])
    else:
        sub = info[info.new_iban.fillna("") != ""]
        df = pd.DataFrame({"entity": sub.entity, "party_id": sub.party_id, "document_type": sub.document_type,
                           "new_iban": sub.new_iban, "old_iban": sub.old_iban.fillna(""),
                           "effective_date": sub.iban_effective_date.fillna("")}).reset_index(drop=True)
    m = {}
    for r in df.itertuples():
        for ib in (r.new_iban, r.old_iban):
            if ib:
                m[norm(ib)] = r.party_id
    ctx.cache["pdfinfo_iban_party"] = m
    ctx.cache[k] = df
    return df
