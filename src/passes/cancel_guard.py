"""POLICIES §5.7: an invoice cancelled by a credit note ("anula y sustituye") and its cancelling credit note are never
settled by bank. Remove such rows from any answer (v3 / ar_match sometimes add the zero-sum pair).
Runs after every matching pass, before exc_fix. If a receipt loses all its rows it becomes UNIDENTIFIED.
"""
from data import norm

CANCEL_WORDS = ("ANUL", "CANCEL")


def run(ctx):
    it = ctx.items
    arc = it[(it.item_type == "ARC") & it.reason.astype(str).str.upper().str.contains("|".join(CANCEL_WORDS), na=False)]
    if not len(arc):
        return
    by_doc = {(e, norm(d)): i for i, e, d in zip(it.item_id, it.entity.astype(str), it.doc_number) if i.startswith("ARI")}
    cancels = {}  # cancelling ARC -> the invoice it cancels
    for c, e, rd in zip(arc.item_id, arc.entity.astype(str), arc.related_document):
        cancels[c] = by_doc.get((e, norm(rd))) if isinstance(rd, str) and rd else None
    amounts = dict(zip(ctx.bank.bank_txn_id, ctx.bank.amount))
    n_rows = n_lines = 0
    for t, rows in ctx.results.items():
        ids = {r["item_id"] for r in rows}
        hit = ids & cancels.keys()
        if not hit:
            continue
        # always drop the cancelling credit note; drop the cancelled invoice only if it sits on the same line
        # (zero-sum pair). A cancelled invoice paid alone may have been paid before its cancellation.
        banned = hit | {cancels[c] for c in hit if cancels[c] in ids}
        keep = [r for r in rows if r["item_id"] not in banned]
        n_rows += len(rows) - len(keep)
        if not keep:
            keep = ([{"item_id": "", "amount": "", "category": "UNIDENTIFIED", "exception": "UNIDENTIFIED"}]
                    if amounts.get(t, 0) > 0 else rows)
            n_lines += 1
        ctx.results[t] = keep
    print(f"   cancel_guard: removed {n_rows} rows of cancelled invoices / cancelling credit notes ({n_lines} lines emptied)")
