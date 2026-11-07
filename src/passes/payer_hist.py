"""Payer identification from the payment history of the ordering party (POLICIES §5.1) -- runs AFTER ar_match and
BEFORE ar_multi_late / ar_bigpool. Active only with PAYER_ID2=1.

Many receipts name the payer in a way the alias index does not resolve (truncated bank names, trade names, text
without a customer alias), so ar_multi_late / ar_bigpool skip them as "no payer". The same ordering party has usually
paid before: its normalized name (counterparty_name, or the bank text without digits when that is empty) was already
reconciled to a customer.

History sources (no label of the phase being predicted is read):
  * TRAIN golden allocations (train/golden_allocations.csv.gz + train/bank_transactions.csv.gz);
  * this pipeline's own allocations made so far (ctx.results), only for bank lines booked BEFORE the receipt.
Rule: a normalized name identifies a payer only when, in the history before the receipt, it points to exactly ONE
customer of ONE entity, and that entity is the receipt's entity. Shared names identify nothing.

Interface (no change to ar_multi_late.py / ar_bigpool.py): both identify the payer with
    m.text_words(t)  ->  m.names(entity, words)
on the shared Matcher ctx.cache['ar_matcher']. This pass wraps those two methods on that instance: when names() finds
no exact (score >= 1.0) customer for the receipt being processed, it returns {history payer: 1.0}. Any exact alias
hit keeps priority, so lines that already have a payer are unchanged.
"""
import bisect
import csv
import gzip
import os
import re
import unicodedata
from collections import defaultdict


def name_key(text, counterparty_name):
    s = counterparty_name if isinstance(counterparty_name, str) and counterparty_name.strip() else None
    pre = ""
    if s is None:
        s, pre = (text if isinstance(text, str) else ""), "T:"
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()
    s = " ".join(re.sub(r"[^A-Z ]", " ", s).split())
    if pre:
        s = s[:40]
    return pre + s if s else ""


def _train_history(ctx, party_of, add):
    tdir = os.path.join(ctx.root, "train")
    gpath = os.path.join(tdir, "golden_allocations.csv.gz")
    bpath = os.path.join(tdir, "bank_transactions.csv.gz")
    if not (os.path.exists(gpath) and os.path.exists(bpath)):
        return 0
    items = defaultdict(list)
    with gzip.open(gpath, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["item_id"].startswith("AR"):
                items[r["bank_txn_id"]].append(r["item_id"])
    n = 0
    with gzip.open(bpath, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            its = items.get(r["bank_txn_id"])
            if not its or float(r["amount"] or 0) <= 0:
                continue
            k = name_key(r["text"], r["counterparty_name"])
            for i in its:
                p = party_of.get(i)
                if p:
                    add(k, r["booking_date"], p, r["entity"])
                    n += 1
    return n


def run(ctx):
    if os.environ.get("PAYER_ID2", "0") != "1":
        return
    m = ctx.cache.get("ar_matcher")
    if m is None:
        print("    payer_hist: ar_matcher not in cache (run ar_match first)")
        return
    party_of = ctx.items.party_id.to_dict()
    # name key -> list of (first date seen, party, entity); a pair is kept with its earliest date
    first = defaultdict(dict)

    def add(k, d, p, e):
        if not k:
            return
        cur = first[k].get((p, e))
        if cur is None or d < cur:
            first[k][(p, e)] = d

    n_train = _train_history(ctx, party_of, add)
    bank = ctx.bank
    date_of = dict(zip(bank.bank_txn_id, bank.booking_date))
    n_own = 0
    for t in bank[bank.amount > 0].itertuples(index=False):
        rows = ctx.results.get(t.bank_txn_id)
        if not rows:
            continue
        k = name_key(t.text, t.counterparty_name)
        for r in rows:
            i = r["item_id"]
            if i and i.startswith("AR") and party_of.get(i):
                add(k, t.booking_date, party_of[i], t.entity)
                n_own += 1
    hist = {k: sorted((d, p, e) for (p, e), d in v.items()) for k, v in first.items()}

    def payer_for(t):
        k = name_key(t.text, t.counterparty_name)
        lst = hist.get(k)
        if not lst:
            return None
        j = bisect.bisect_left(lst, (t.booking_date,))   # strictly earlier dates only
        seen = {(p, e) for _, p, e in lst[:j]}
        if len(seen) != 1:
            return None
        p, e = next(iter(seen))
        if e != t.entity or p not in m.ix.cust:
            return None
        return p

    state = {"t": None}
    orig_tw, orig_names = m.text_words, m.names
    stats = defaultdict(int)

    def text_words(t):
        state["t"] = t
        return orig_tw(t)

    def names(entity, words):
        res = orig_names(entity, words)
        t = state["t"]
        if t is None or getattr(t, "entity", None) != entity or any(s >= 1.0 for s in res.values()):
            return res
        if getattr(t, "amount", 0) <= 0 or t.bank_txn_id in ctx.results:
            return res
        p = payer_for(t)
        if p is None:
            stats["no_hist"] += 1
            return res
        stats["hist_payer"] += 1
        return {**res, p: 1.0}

    m.text_words, m.names = text_words, names
    print(f"    payer_hist: history train={n_train} own={n_own} names={len(hist)}  (wrapped matcher; stats at end of run)")
    ctx.cache["payer_hist_stats"] = stats
    del date_of
