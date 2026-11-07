"""Shared helpers of headroom.py / inspect_txn.py (read-only diagnostics; nothing here feeds the pipeline).

    Run        one dev run: golden (+segment), submission, why labels, bank lines, dataset dir
    Replay     open balance of every item at the moment of each bank line, replaying the golden of earlier lines
    PayerId    who the PIPELINE thinks the payer is (see PayerId): its own answer, else IBAN / name / history
"""
import bisect
import csv
import gzip
import json
import os
import re
import sys
from collections import Counter, defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dev  # noqa: E402  (scorer, EXC, read_sub, txn_ok, fmt_g, fmt_p)

ROOT = dev.ROOT
S = dev.S
sys.path.insert(0, os.path.join(ROOT, "src"))
try:
    from ar_payer import payer_name  # the pipeline's own name extractor (generic format patterns), read-only
except Exception:  # pragma: no cover
    payer_name = None


def nrm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def run_dir_of(run):
    return run if os.path.isdir(run) else os.path.join(ROOT, "dev_runs", run)


class Run:
    def __init__(self, run, data=None, need_sub=True):
        self.dir = run_dir_of(run)
        self.name = os.path.basename(os.path.normpath(self.dir))
        self.res = json.load(open(os.path.join(self.dir, "result.json")))
        self.D = os.path.expanduser(data or self.res["meta"]["dataset"])
        self.bt = os.path.join(self.D, "live", "bank_transactions.csv.gz")
        cats = [r["category"] for r in csv.DictReader(open(os.path.join(self.D, "masters", "categories.csv"), encoding="utf-8"))]
        self.golden = S.load_golden(os.path.join(self.D, "golden_dev.csv"), self.bt, dev.EXC, cats)
        self.pred = dev.read_sub(os.path.join(self.dir, "sub.csv.gz")) if need_sub else {}
        self.why = {}
        wp = os.path.join(self.dir, "why.csv")
        if os.path.exists(wp):
            with open(wp, newline="", encoding="utf-8") as fh:
                self.why = {r["bank_txn_id"]: r["why"] for r in csv.DictReader(fh)}
        self.bank = pd.read_csv(self.bt, dtype=str, keep_default_na=False).set_index("bank_txn_id")

    def ok(self, t):
        return dev.txn_ok(self.golden.txns[t], self.pred.get(t))

    def segment(self, t):
        return self.golden.txns[t][3]

    def wrong_in(self, seg):
        return sorted(t for t, g in self.golden.txns.items() if g[3] == seg and not self.ok(t))


class Replay:
    """open_at(item, txn): open amount of the item just before the bank line, replaying the golden of earlier lines
    (what a perfect allocator of the previous lines would have left open)."""

    def __init__(self, run):
        D, golden, bank = run.D, run.golden, run.bank
        op = pd.read_csv(os.path.join(D, "live", "open_items_at_start.csv.gz"), dtype=str, keep_default_na=False)
        cols = ["item_id", "party_id", "document_number", "issue_date", "currency", "total_amount", "open_amount"]
        newi = []
        for fn, pc, dc in (("ar_invoices.csv.gz", "customer_id", "document_number"),
                           ("ap_invoices.csv.gz", "supplier_id", "supplier_invoice_number"),
                           ("credit_notes.csv.gz", "party_id", "document_number")):
            d = pd.read_csv(os.path.join(D, "live", fn), dtype=str, keep_default_na=False)
            newi.append(pd.DataFrame({"item_id": d.item_id, "party_id": d[pc], "document_number": d[dc], "issue_date": d.issue_date,
                                      "currency": d.currency, "total_amount": d.total_amount, "open_amount": d.total_amount}))
        items = pd.concat([op[cols]] + newi, ignore_index=True).drop_duplicates("item_id").set_index("item_id")
        items["open0"] = items.open_amount.astype(float)
        self.items = items
        self.golden, self.bank = golden, bank
        self.by_party = defaultdict(list)
        for iid, pid in zip(items.index, items.party_id):
            self.by_party[pid].append(iid)
        self.bdate = bank.booking_date.to_dict()
        ev = defaultdict(list)
        for t, g in golden.txns.items():
            for iid, (amt, exc) in g[0].items():
                delta = amt if exc == "RETURNED" else (0.0 if exc == "DUPLICATE_PAYMENT" else -amt)
                ev[iid].append((self.bdate[t], t, delta))
        for iid in ev:
            ev[iid].sort()
        self.ev = ev
        self.evkeys = {iid: [(d, t) for d, t, _ in v] for iid, v in ev.items()}

    def open_at(self, iid, t):
        o = self.items.at[iid, "open0"]
        k = bisect.bisect_left(self.evkeys.get(iid, []), (self.bdate[t], t))
        return o + sum(x[2] for x in self.ev.get(iid, [])[:k])

    def party_of(self, iid):
        return self.items.at[iid, "party_id"] if iid in self.items.index else None

    def candidates(self, party, t):
        """[(item_id, open_amount, in_range)] open items of `party` issued before the bank line"""
        amt = abs(float(self.bank.at[t, "amount"]))
        out = []
        for iid in self.by_party.get(party, []):
            if self.items.at[iid, "issue_date"] > self.bdate[t]:
                continue
            o = self.open_at(iid, t)
            if o > 0.005:
                same_cur = self.bank.at[t, "currency"] == self.items.at[iid, "currency"]
                out.append((iid, o, (not same_cur) or o <= amt * 1.01))
        return out

    def refs_visible(self, t, iids):
        """how many of `iids` have their document number visible in the bank text / references"""
        cols = [c for c in ("text", "ref1", "ref2", "remittance_info", "end_to_end_id") if c in self.bank.columns]
        text = nrm(" ".join(str(self.bank.at[t, c]) for c in cols))
        digits = re.sub(r"\D", "", text)
        n = 0
        for iid in iids:
            if iid not in self.items.index:
                continue
            dn = nrm(self.items.at[iid, "document_number"])
            dg = re.sub(r"\D", "", dn)
            if (len(dn) >= 5 and dn in text) or (len(dg) >= 5 and dg[-6:] in digits):
                n += 1
        return n


class PayerId:
    """The payer the PIPELINE identifies for a bank line (never the golden's).

    The pipeline does not export its payer, so it is reconstructed from what it can see, in this order:
      1. 'pipeline'  the party of the items the submission itself allocated (what the pipeline decided);
      2. 'iban'      counterparty_account -> party (masters' IBANs, then TRAIN history: that account paid items of that party);
      3. 'nombre'    counterparty_name, else the name the pipeline's own extractor (src/ar_payer.py) finds in the text ->
                     party (TRAIN history of that name, then exact normalised name / trade name in the masters, same entity);
      4. None        the pipeline has no payer for this line.
    Only TRAIN (before the window) and the masters are used: no golden of the window, no segment.
    """

    def __init__(self, run, replay):
        self.run, self.rp = run, replay
        D = run.D
        ent = lambda e, k: (e, k)  # noqa: E731
        self.iban, self.name = {}, {}
        for fn, pc in (("customers.csv", "customer_id"), ("suppliers.csv", "supplier_id")):
            m = pd.read_csv(os.path.join(D, "masters", fn), dtype=str, keep_default_na=False)
            for r in m.to_dict("records"):
                if r.get("iban"):
                    self.iban[ent(r["entity"], nrm(r["iban"]))] = r[pc]
                for c in ("name", "trade_name"):
                    if r.get(c):
                        self.name.setdefault(ent(r["entity"], nrm(r[c])), r[pc])
        # TRAIN history: account / name -> party of the items it paid
        party = {}
        for fn, pc in (("ar_invoices.csv.gz", "customer_id"), ("ap_invoices.csv.gz", "supplier_id"), ("credit_notes.csv.gz", "party_id")):
            d = pd.read_csv(os.path.join(D, "train", fn), usecols=["item_id", pc], dtype=str)
            party.update(zip(d.item_id, d[pc]))
        tb = pd.read_csv(os.path.join(D, "train", "bank_transactions.csv.gz"),
                         usecols=["bank_txn_id", "entity", "counterparty_name", "counterparty_account"], dtype=str, keep_default_na=False)
        tb = tb[(tb.counterparty_name != "") | (tb.counterparty_account != "")].set_index("bank_txn_id")
        gd = pd.read_csv(os.path.join(D, "train", "golden_allocations.csv.gz"), usecols=["bank_txn_id", "item_id"], dtype=str,
                         keep_default_na=False)
        gd = gd[(gd.item_id != "") & gd.bank_txn_id.isin(tb.index)]
        gd["party"] = gd.item_id.map(party)
        gd = gd.dropna(subset=["party"]).merge(tb[["entity", "counterparty_name", "counterparty_account"]], left_on="bank_txn_id",
                                               right_index=True)
        self.hist = {}
        for kind, col in (("acc", "counterparty_account"), ("nom", "counterparty_name")):
            d = gd[gd[col] != ""].copy()
            d["k"] = d[col].map(nrm)
            c = d.groupby(["entity", "k", "party"]).size().reset_index(name="n").sort_values("n", ascending=False)
            for e, k, p_ in zip(c.entity, c.k, c.party):
                self.hist.setdefault((kind, e, k), p_)

    def identify(self, t):
        """-> (party or None, how)"""
        p = self.run.pred.get(t)
        if p and p["items"]:
            ps = Counter(self.rp.party_of(i) for i in p["items"] if self.rp.party_of(i))
            if ps:
                return ps.most_common(1)[0][0], "pipeline"
        b = self.run.bank.loc[t]
        e = b["entity"]
        acc = nrm(b.get("counterparty_account", ""))
        if acc:
            for v in (self.iban.get((e, acc)), self.hist.get(("acc", e, acc))):
                if v:
                    return v, "iban"
        names = []
        if b.get("counterparty_name"):
            names.append(b["counterparty_name"])
        if payer_name is not None:
            n = payer_name(type("T", (), {"counterparty_name": b.get("counterparty_name", ""), "text": b.get("text", "")})())
            if n:
                names.append(n)
        for n in names:
            k = nrm(n)
            for v in (self.hist.get(("nom", e, k)), self.name.get((e, k))):
                if v:
                    return v, "nombre"
        return None, "sin pagador"


# ------------------------------------------------------------------ blocks (reporting only)
BLOCKS_FILE = os.path.join(HERE, "blocks.yaml")


def load_blocks(path=None):
    """blocks.yaml -> {block: {"segments": [patterns], "classes": [names]}} in file order. Minimal parser for the documented subset."""
    blocks, cur, key = {}, None, None
    for raw in open(path or BLOCKS_FILE, encoding="utf-8"):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        ind = len(line) - len(line.lstrip())
        body = line.strip()
        if ind == 0 and body.endswith(":"):
            cur = blocks.setdefault(body[:-1], {"segments": [], "classes": []})
            key = None
        elif ind > 0 and cur is not None and body.endswith(":"):
            key = body[:-1]
            cur.setdefault(key, [])
        elif ind > 0 and cur is not None and key and body.startswith("- "):
            cur[key].append(body[2:].strip())
        elif ind > 0 and cur is not None and key and body == "[]":
            pass
        elif body.endswith("[]") and ":" in body:  # "classes: []"
            cur.setdefault(body.split(":")[0].strip(), [])
        else:
            raise ValueError(f"blocks.yaml: cannot parse line {raw!r}")
    return blocks


def block_of(name, blocks, kind):
    """first block whose `kind` ('segments' | 'classes') lists `name` (fnmatch globs allowed); else 'sin_bloque'"""
    import fnmatch
    for b, d in blocks.items():
        if any(fnmatch.fnmatchcase(name, pat) for pat in d.get(kind, [])):
            return b
    return "sin_bloque"
