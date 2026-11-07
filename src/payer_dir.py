"""Payer directory learned from TRAIN labels: which customer each paying IBAN / payer name belonged to.

Renamed customers, trade names, parents paying for subsidiaries, factors and new IBANs all appear in the history of
labelled receipts, while customers.csv only shows the current name and one IBAN.

Leakage control: only TRAIN bank lines booked BEFORE `cutoff` are used (cutoff = first booking date of the bank lines
being reconciled; LIVE/FINAL simply use all of TRAIN). Keys mapping to more than one customer are dropped.
"""
import os

import pandas as pd

from data import CACHE, _read, load_bank, load_items, norm
from ar_index import core_name
from ar_payer import payer_name


def build(ctx):
    cutoff = ctx.bank.booking_date.min() if len(ctx.bank) else "9999"
    cutoff = min(cutoff, "2025-07-01")
    p = os.path.join(CACHE, f"payer_dir_{cutoff}.parquet")
    if os.path.exists(p):
        return pd.read_parquet(p)
    g = pd.read_parquet(os.path.join(CACHE, "golden_train.parquet"), columns=["bank_txn_id", "item_id"]) \
        if os.path.exists(os.path.join(CACHE, "golden_train.parquet")) else \
        _read(os.path.join(ctx.root, "train", "golden_allocations.csv.gz"))[["bank_txn_id", "item_id"]]
    g = g[g.item_id.str.startswith("AR")]
    it = load_items(ctx.root, "train")[["item_id", "party_id"]]
    party = g.merge(it, on="item_id").groupby("bank_txn_id").party_id.agg(lambda x: x.iloc[0] if x.nunique() == 1 else None)
    party = party.dropna()
    b = load_bank(ctx.root, "train")
    b = b[(b.booking_date < cutoff) & (b.amount > 0) & b.bank_txn_id.isin(party.index)]
    rows = []
    for t in b.itertuples(index=False):
        pid = party[t.bank_txn_id]
        ib = norm(t.counterparty_account) if isinstance(t.counterparty_account, str) else ""
        if ib:
            rows.append(("iban", t.entity, ib, pid))
        nm = payer_name(t)
        c = core_name(nm) if nm else ""
        if len(c) >= 5:
            rows.append(("name", t.entity, c, pid))
    d = pd.DataFrame(rows, columns=["kind", "entity", "key", "party_id"])
    n = d.groupby(["kind", "entity", "key"]).party_id.nunique()
    ok = n[n == 1].index
    d = d.set_index(["kind", "entity", "key"]).loc[ok].reset_index().drop_duplicates()
    d.to_parquet(p, index=False)
    return d


def apply(ctx, ix):
    """Add learned IBANs / name aliases to an ar_index.ARIndex."""
    try:
        d = build(ctx)
    except Exception as e:  # never break the run
        print(f"   payer_dir: skipped ({e})")
        return
    n_ib = n_nm = 0
    for kind, ent, key, pid in d.itertuples(index=False):
        if pid not in ix.cust:
            continue
        if kind == "iban":
            if pid not in ix.iban[key]:
                ix.iban[key].add(pid)
                n_ib += 1
        else:
            before = len(ix.alias[(ent, key.split()[0])])
            ix.add_alias(ent, key, pid)
            n_nm += len(ix.alias[(ent, key.split()[0])]) - before
    print(f"   payer_dir: +{n_ib} IBANs, +{n_nm} name aliases (history before {ctx.bank.booking_date.min()})")
