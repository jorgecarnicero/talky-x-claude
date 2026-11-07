"""Analysis of the 'pocket' left by a dev run: lines without any pass (answered UNIDENTIFIED) and lines answered by v3rest.

    python solution/tools/bolsa.py v35 [--data ~/hackdata/data_dev] > notes/bolsa_v35.md

Reads dev_runs/<run>/{sub.csv.gz,why.csv}, <data>/golden_dev.csv, the opening table and the window documents. Diagnostic only
(it reads the golden and the segment column to explain failures, never to produce an answer).
"""
import argparse
import bisect
import csv
import gzip
import os
import re
import sys
from collections import Counter, defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dev  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("run")
ap.add_argument("--data", default=os.path.expanduser("~/hackdata/data_dev"))
a = ap.parse_args()
D = a.data
RUN = os.path.join(dev.ROOT, "dev_runs", a.run)
OUT = []


def p(s=""):
    OUT.append(s)


def table(df, index=True):
    cols = ([df.index.name or ""] if index else []) + [str(c) for c in df.columns]
    p("| " + " | ".join(cols) + " |")
    p("|" + "|".join(["---"] + ["---:"] * (len(cols) - 1)) + "|")
    for tup in df.itertuples(index=index):
        vals = list(tup)
        p("| " + " | ".join(f"{v:.1f}" if isinstance(v, float) and v != int(v) else (str(int(v)) if isinstance(v, float) else str(v)) for v in vals) + " |")
    p()


def nrm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


# ------------------------------------------------------------------ load
cats = [r["category"] for r in csv.DictReader(open(os.path.join(D, "masters", "categories.csv"), encoding="utf-8"))]
bt = os.path.join(D, "live", "bank_transactions.csv.gz")
golden = dev.S.load_golden(os.path.join(D, "golden_dev.csv"), bt, dev.EXC, cats)
pred = dev.read_sub(os.path.join(RUN, "sub.csv.gz"))
why = {r["bank_txn_id"]: r["why"] for r in csv.DictReader(open(os.path.join(RUN, "why.csv"), encoding="utf-8"))}
bank = pd.read_csv(bt, dtype=str, keep_default_na=False).set_index("bank_txn_id")
ok = {t: dev.txn_ok(g, pred.get(t)) for t, g in golden.txns.items()}
setA = {t for t in golden.txns if dev.is_unexplained(why.get(t, ""), pred.get(t))}
setB = {t for t in golden.txns if why.get(t, "").startswith("v3rest")}
GA = lambda t: golden.txns[t]  # noqa: E731

p(f"# Pocket of run `{a.run}`: lines without any pass and v3rest lines")
p()
p(f"- Without any pass (answered UNIDENTIFIED / no matcher): **{len(setA)}** lines. v3rest: **{len(setB)}** lines. "
  f"Overlap {len(setA & setB)}; only without-pass {len(setA - setB)}, only v3rest {len(setB - setA)}.")
p(f"- Correct: without-pass {sum(ok[t] for t in setA)}, v3rest {sum(ok[t] for t in setB)}. Union analysed below: "
  f"{len(setA | setB)} lines, {sum(not ok[t] for t in setA | setB)} wrong.")
p()
p("Method: diagnostic only. The golden (and its segment column) is read to explain failures; the 'candidates' use the party of the "
  "golden items as the payer and replay the golden of earlier lines to know each item's open balance at that moment, so they "
  "describe what a perfect payer identification would face.")
p()
p("{{SUMMARY}}")
p()


# ------------------------------------------------------------------ 1. by segment
def by_segment(S, title):
    rows = defaultdict(lambda: [0, 0])
    for t in S:
        r = rows[GA(t)[3]]
        r[0] += 1
        r[1] += ok[t]
    df = pd.DataFrame([(k, n, o, n - o) for k, (n, o) in rows.items()], columns=["segment", "lines", "correct", "wrong"])
    df = df.sort_values("wrong", ascending=False).set_index("segment")
    p(f"### {title}")
    p()
    table(df)


p("## 1. By golden segment")
p()
by_segment(setA, f"Without any pass ({len(setA)})")
by_segment(setB, f"v3rest ({len(setB)})")

# ------------------------------------------------------------------ 2. what the golden has on the wrong lines
U = setA | setB
W = [t for t in U if not ok[t]]
w_items = [t for t in W if GA(t)[0]]
w_noitem = [t for t in W if not GA(t)[0]]
p("## 2. Wrong lines: does the golden have items (pipeline did not find them) or a category / UNIDENTIFIED (pipeline chose wrong)?")
p()
p(f"Union of the two sets, wrong lines = {len(W)}: golden **has items** in {len(w_items)} "
  f"({100 * len(w_items) / max(len(W), 1):.1f} %), golden is **no-item** (category / UNIDENTIFIED) in {len(w_noitem)} "
  f"({100 * len(w_noitem) / max(len(W), 1):.1f} %).")
p()


def pred_label(t):
    q = pred.get(t)
    if q is None:
        return "(missing)"
    if q["cat"] is not None:
        return f"{q['cat']}/{q['exc'] or '-'}"
    return "items"


p("### 2a. Golden has items: golden exception set vs what the pipeline answered")
p()
c = Counter()
for t in w_items:
    ex = sorted({e or "-" for _, e in GA(t)[0].values()})
    c[("+".join(ex), pred_label(t))] += 1
df = pd.DataFrame([(g, pl, n) for (g, pl), n in c.most_common()], columns=["golden exception(s)", "pipeline answered", "lines"])
table(df.head(25), index=False)
c = Counter(len(GA(t)[0]) for t in w_items)
p("Items per line in the golden (wrong lines): " + ", ".join(f"{k}: {v}" for k, v in sorted(c.items())[:12]))
p()
p("### 2b. Golden is a category / UNIDENTIFIED: golden category/exception vs pipeline answer")
p()
c = Counter()
for t in w_noitem:
    g = GA(t)
    c[(f"{g[1] or '-'}/{g[2] or '-'}", pred_label(t))] += 1
df = pd.DataFrame([(g, pl, n) for (g, pl), n in c.most_common()], columns=["golden category/exception", "pipeline answered", "lines"])
table(df.head(25), index=False)

# ------------------------------------------------------------------ 3. recall vs selection
p("## 3. Wrong lines with items in the golden: were the items available, and how many candidates?")
p()
op = pd.read_csv(os.path.join(D, "live", "open_items_at_start.csv.gz"), dtype=str, keep_default_na=False)
newi = []
for fn, typ, pc, dc in (("ar_invoices.csv.gz", "ARI", "customer_id", "document_number"),
                        ("ap_invoices.csv.gz", "API", "supplier_id", "supplier_invoice_number"),
                        ("credit_notes.csv.gz", None, "party_id", "document_number")):
    d = pd.read_csv(os.path.join(D, "live", fn), dtype=str, keep_default_na=False)
    newi.append(pd.DataFrame({"item_id": d.item_id, "party_id": d[pc], "document_number": d[dc], "issue_date": d.issue_date,
                              "currency": d.currency, "total_amount": d.total_amount, "open_amount": d.total_amount}))
items = pd.concat([op[["item_id", "party_id", "document_number", "issue_date", "currency", "total_amount", "open_amount"]]]
                  + newi, ignore_index=True)
in_open = set(op.item_id)
in_new = set(pd.concat(newi).item_id)
train_ids = set()
for fn in ("ar_invoices.csv.gz", "ap_invoices.csv.gz", "credit_notes.csv.gz"):
    train_ids |= set(pd.read_csv(os.path.join(D, "train", fn), usecols=["item_id"], dtype=str).item_id)
items = items.drop_duplicates("item_id").set_index("item_id")
items["open0"] = items.open_amount.astype(float)
by_party = defaultdict(list)
for iid, pid in zip(items.index, items.party_id):
    by_party[pid].append(iid)

# golden replay: allocation events per item, ordered by booking date
bdate = bank.booking_date.to_dict()
ev = defaultdict(list)
for t, g in golden.txns.items():
    for iid, (amt, exc) in g[0].items():
        delta = amt if exc == "RETURNED" else (0.0 if exc == "DUPLICATE_PAYMENT" else -amt)
        ev[iid].append((bdate[t], t, delta))
for iid in ev:
    ev[iid].sort()
evkeys = {iid: [(d, t) for d, t, _ in v] for iid, v in ev.items()}


def open_at(iid, t):
    """open amount of the item just before bank line t, replaying the golden of earlier lines"""
    o = items.at[iid, "open0"]
    k = bisect.bisect_left(evkeys.get(iid, []), (bdate[t], t))
    return o + sum(x[2] for x in ev.get(iid, [])[:k])


status = Counter()
rows = []
for t in w_items:
    gi = GA(t)[0]
    st = []
    for iid, (amt, exc) in gi.items():
        if iid in in_open:
            st.append("open at start")
        elif iid in in_new:
            st.append("issued in window")
        elif iid in train_ids:
            st.append("closed before start")
        else:
            st.append("unknown")
    kinds = set(st)
    status["all open at start" if kinds == {"open at start"} else "all in ledger (open or issued in window)"
           if kinds <= {"open at start", "issued in window"} else "some not open at start"] += 1
    ok_items = [iid for iid in gi if iid in items.index]
    if not ok_items:
        continue
    parties = Counter(items.at[iid, "party_id"] for iid in ok_items)
    party = parties.most_common(1)[0][0]
    cur = items.at[ok_items[0], "currency"]
    amt = abs(float(bank.at[t, "amount"]))
    same_cur = bank.at[t, "currency"] == cur
    cand, cand_rng = [], []
    for iid in by_party[party]:
        if items.at[iid, "issue_date"] > bdate[t]:
            continue
        o = open_at(iid, t)
        if o > 0.005:
            cand.append(iid)
            if (not same_cur) or o <= amt * 1.01:
                cand_rng.append(iid)
    avail = sum(1 for iid in gi if iid in set(cand))
    text = nrm(" ".join(str(bank.at[t, c]) for c in ("text", "ref1", "ref2", "remittance_info", "end_to_end_id")))
    refs = 0
    for iid in gi:
        dn = nrm(items.at[iid, "document_number"]) if iid in items.index else ""
        dg = re.sub(r"\D", "", dn)
        if (len(dn) >= 5 and dn in text) or (len(dg) >= 5 and dg[-6:] in re.sub(r"\D", "", text)):
            refs += 1
    rows.append({"txn": t, "segment": GA(t)[3], "n_golden": len(gi), "avail": avail, "cand_payer": len(cand),
                 "cand_range": len(cand_rng), "refs_in_text": refs, "pipeline": pred_label(t),
                 "counterparty_known": bank.at[t, "counterparty_name"] != "" or bank.at[t, "counterparty_account"] != ""})
R = pd.DataFrame(rows)
p("Where are the golden items on those lines? (per line)")
p()
table(pd.DataFrame([(k, v) for k, v in status.most_common()], columns=["state", "lines"]), index=False)
n = len(R)
p(f"Replay of the golden before each line (open balance at that moment, payer = party of the golden items), {n} lines:")
p()
p(f"- all golden items open at that moment (so a matcher could in principle find them): **{(R.avail == R.n_golden).sum()}** "
  f"({100 * (R.avail == R.n_golden).mean():.1f} %); some missing: {(R.avail < R.n_golden).sum()}.")
p(f"- at least one golden document number visible in the bank text / refs: **{(R.refs_in_text > 0).sum()}** "
  f"({100 * (R.refs_in_text > 0).mean():.1f} %); all of them visible: {(R.refs_in_text == R.n_golden).sum()}.")
p(f"- counterparty name or account present on the bank line: {R.counterparty_known.sum()} ({100 * R.counterparty_known.mean():.1f} %).")
p()
bins = [0, 1, 2, 5, 10, 20, 50, 100, 10**9]
labs = ["1", "2", "3-5", "6-10", "11-20", "21-50", "51-100", ">100"]
for col, ttl in (("cand_payer", "open items of the payer at that moment"),
                 ("cand_range", "of those, open amount <= payment (x1.01)")):
    p(f"Candidates per line: {ttl}")
    p()
    d = pd.cut(R[col], bins=[-1] + bins[1:], labels=["0"] + labs[0:0] or None) if False else pd.cut(R[col], bins=[-1, 0] + bins[1:], labels=["0"] + labs)
    t_ = d.value_counts().reindex(["0"] + labs).fillna(0).astype(int)
    table(pd.DataFrame({"lines": t_, "of which refs visible": R.groupby(d, observed=False).apply(lambda x: int((x.refs_in_text > 0).sum())).reindex(["0"] + labs).fillna(0).astype(int)}))
p("n_golden (items to find per line) vs candidates in range, median / p90 by golden segment (top 12 by lines):")
p()
g = R.groupby("segment").agg(lines=("txn", "count"), n_golden=("n_golden", "median"), cand_payer_med=("cand_payer", "median"),
                             cand_payer_p90=("cand_payer", lambda x: x.quantile(.9)), cand_range_med=("cand_range", "median"),
                             refs_visible=("refs_in_text", lambda x: int((x > 0).sum()))).sort_values("lines", ascending=False).head(12)
table(g)
easy = ((R.avail == R.n_golden) & (R.cand_range <= 5)).sum()
refs_ = ((R.avail == R.n_golden) & (R.refs_in_text > 0)).sum()
p("Reading:")
p()
p(f"- *Recall of the matcher* (items available, reference visible or few candidates): {int(((R.avail == R.n_golden) & ((R.refs_in_text > 0) | (R.cand_range <= 5))).sum())} "
  f"lines ({refs_} with a visible reference, {easy} with <= 5 candidates in range).")
p(f"- *Selection / ambiguity* (items available, no visible reference, > 5 candidates in range): "
  f"{int(((R.avail == R.n_golden) & (R.refs_in_text == 0) & (R.cand_range > 5)).sum())} lines.")
p(f"- *Not findable by replay* (a golden item is not open at that moment, e.g. duplicates, returns, items settled by earlier lines): "
  f"{int((R.avail < R.n_golden).sum())} lines.")
p()
R.to_csv(os.path.join(RUN, "bolsa_candidates.csv"), index=False)

# ------------------------------------------------------------------ 4. OVERPAYMENT
p("## 4. OVERPAYMENT confusion on the dev window")
p()
gp = {(t, i): e for t, g in golden.txns.items() for i, (_, e) in g[0].items()}
pp = {(t, i): e for t, q in pred.items() for i, (_, e) in q["items"].items()}
keys = {k for k, e in gp.items() if e == "OVERPAYMENT"} | {k for k, e in pp.items() if e == "OVERPAYMENT"}
c = Counter()
for k in keys:
    g_ = gp.get(k, "(pair absent)")
    p_ = pp.get(k, "(pair absent)")
    c[(g_ or "-", p_ or "-")] += 1
df = pd.DataFrame([(g_, p_, n) for (g_, p_), n in c.most_common()], columns=["golden exception", "pipeline exception", "pairs"])
p("Item-pair level (pairs where golden or pipeline says OVERPAYMENT; '-' = no exception, '(pair absent)' = pair not in that file):")
p()
table(df, index=False)
tp_ = c[("OVERPAYMENT", "OVERPAYMENT")]
fn_ = sum(v for (g_, p_), v in c.items() if g_ == "OVERPAYMENT" and p_ != "OVERPAYMENT")
fp_ = sum(v for (g_, p_), v in c.items() if p_ == "OVERPAYMENT" and g_ != "OVERPAYMENT")
p(f"TP {tp_}, FN {fn_}, FP {fp_}: precision {tp_ / max(tp_ + fp_, 1):.3f}, recall {tp_ / max(tp_ + fn_, 1):.3f}.")
p()
fnl = sorted({t for (t, i) in keys if gp.get((t, i)) == "OVERPAYMENT" and pp.get((t, i)) != "OVERPAYMENT"})
c = Counter()
for t in fnl:
    q = pred.get(t)
    gi = GA(t)[0]
    if q is None:
        c["line missing"] += 1
    elif q["cat"] is not None:
        c[f"answered {q['cat']}/{q['exc'] or '-'} (no items)"] += 1
    elif set(q["items"]) == set(gi):
        c["same items, other exception: " + "/".join(sorted({e or '-' for _, e in q['items'].values()}))] += 1
    elif set(q["items"]) & set(gi):
        c["partly the same items"] += 1
    else:
        c["different items"] += 1
p(f"False negatives by what the pipeline did on the line ({len(fnl)} lines):")
p()
table(pd.DataFrame(c.most_common(), columns=["pipeline answer", "lines"]), index=False)
c = Counter()
for t in fnl:
    c[GA(t)[3]] += 1
p("Segments of the missed OVERPAYMENT lines: " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
p()
fpl = sorted({t for (t, i) in keys if pp.get((t, i)) == "OVERPAYMENT" and gp.get((t, i)) != "OVERPAYMENT"})
c = Counter()
for t in fpl:
    g = GA(t)
    gi = g[0]
    if not gi:
        c[f"golden {g[1] or '-'}/{g[2] or '-'} (no items)"] += 1
    else:
        c["golden items, exceptions " + "+".join(sorted({e or '-' for _, e in gi.values()})) + "; seg " + g[3]] += 1
p(f"False positives by what the golden has on the line ({len(fpl)} lines):")
p()
table(pd.DataFrame(c.most_common(12), columns=["golden", "lines"]), index=False)
why_c = Counter(why.get(t, "").split(":")[0] + ":" + (why.get(t, "").split(":")[1] if ":" in why.get(t, "") else "") for t in
                {t for (t, i) in keys if pp.get((t, i)) == "OVERPAYMENT"})
p("Pass / matcher that emitted OVERPAYMENT: " + ", ".join(f"{k} {v}" for k, v in why_c.most_common(8)))
p()
print("\n".join(OUT))
