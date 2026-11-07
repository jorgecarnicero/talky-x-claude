"""v3import restricted to v3 answers with strong evidence (runs BEFORE our matchers; the rest of v3 is imported later).

LIVE v7d (v3import after our matchers) gained on exceptions/credit notes but lost on ar_1to1, ar_1toN_text and
ar_same_amount_fifo, where v3's own answer was better. So import early only v3 lines where
  (a) every item's printed number (full, or its trailing >= 5 digits) appears in the bank text, or
  (b) V3_STRONG_SINGLE=1 and v3 answers a single item whose open amount equals the cash exactly (same currency).
"""
import os
import re

from data import norm, full_text
from passes.v3import import _load

SINGLE = os.environ.get("V3_STRONG_SINGLE", "1") == "1"


def _referenced(doc, txt_norm, digits):
    d = norm(doc)
    if len(d) >= 5 and d in txt_norm:
        return True
    dg = re.sub(r"\D", "", doc or "").lstrip("0")
    return len(dg) >= 5 and dg[-6:] in digits


def run(ctx):
    v3 = _load(ctx)
    it = ctx.items
    docs = dict(zip(it.item_id, it.doc_number))
    curr = dict(zip(it.item_id, it.currency.astype(str)))
    n_ref = n_single = 0
    for t in ctx.bank.itertuples(index=False):
        if ctx.done(t.bank_txn_id):
            continue
        rows = v3.get(t.bank_txn_id)
        if not rows or not rows[0]["item_id"]:
            continue
        ids = [r["item_id"] for r in rows]
        if any(i not in ctx.open and r["exception"] not in ("DUPLICATE_PAYMENT", "RETURNED") for i, r in zip(ids, rows)):
            continue  # item already cleared in our ledger: leave the line to later passes
        txt = full_text(t._asdict())
        tn = norm(txt)
        digits = " ".join(re.findall(r"\d{5,}", txt))
        strong = all(_referenced(docs.get(i, ""), tn, digits) for i in ids)
        kind = "ref"
        if not strong and SINGLE and len(ids) == 1 and rows[0]["exception"] in ("",) \
                and curr.get(ids[0]) == t.currency and abs(ctx.open.get(ids[0], -1) - abs(t.amount)) <= 0.005:
            strong, kind = True, "single"
        if not strong:
            continue
        ctx.allocate(t.bank_txn_id, [{"item_id": r["item_id"], "amount": float(r["amount"]), "exception": r["exception"]}
                                     for r in rows], "v3strong:" + kind)
        n_ref += kind == "ref"
        n_single += kind == "single"
    print(f"   v3import_strong: ref {n_ref}, single {n_single}")
