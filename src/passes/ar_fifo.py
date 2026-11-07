"""Same-amount AR receipts resolved per POLICIES §5.6 (runs before v3import).

A receipt is claimed when
  (a) the payer is a single customer of the entity, identified by IBAN (master or learned directory) and/or an exact
      name alias (no conflict between the two),
  (b) the text carries no usable invoice reference (no document/PO token of that customer or of the entity),
  (c) the cash equals exactly the open amount of >= 2 open ARI invoices of that customer in the bank currency, and
  (d) the customer has no open credit note / no other single open item of that exact amount (other types).
Pick: text names a service month M -> invoice issued in month M+1 (monthly fees are invoiced at the beginning of the
following month); otherwise the OLDEST open invoice (issue date, due date, document number).
Lines are processed in booking order against the running ledger ctx.open.
"""
import os
import re
from collections import defaultdict
from datetime import date, timedelta

from data import norm, norm_words
from ar_index import ARIndex, IBAN_RE, AMT_RE, SPLIT_RE, MONTHS, find_month, LEGAL

CONNECT = {"Y", "AND", "ET", "UND", "E", "I"}
HORIZON = int(os.environ.get("FIFO_HORIZON", "200"))
LOOKAHEAD = os.environ.get("FIFO_LOOKAHEAD", "1") == "1"
PLAIN = os.environ.get("FIFO_PLAIN", "0") == "1"
SINGLE = os.environ.get("FIFO_SINGLE", "0") == "1"


def _tokens(s):
    s = AMT_RE.sub(" ", s.upper())
    toks = []
    for raw in re.split(r"[\s|,;:*\\()+#=]+", s):
        if "/" in raw:
            n = norm(raw)
            if len(n) >= 6 and any(c.isdigit() for c in n):
                toks.append(n)
    for raw in SPLIT_RE.split(s):
        n = norm(raw)
        if len(n) >= 4 and any(c.isdigit() for c in n):
            toks.append(n)
    return toks


_LONG = [(n, m) for n, m in MONTHS.items() if len(n) >= 6]


def month_of(words):
    """find_month + truncated month names cut by the bank field width ('SERVICIOS OCTUBR')."""
    r = find_month(words)
    if r:
        return r
    for w in words:
        if len(w) >= 5 and w.isalpha():
            ms = {m for n, m in _LONG if n.startswith(w) and n != w}
            if len(ms) == 1:
                return ms.pop(), None
    return None


def _d(s):
    return date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def _is_year(tk):
    return tk.isdigit() and len(tk) == 4 and tk[:2] in ("19", "20")


class Fifo:
    def __init__(self, ctx):
        self.ctx = ctx
        self.ix = ARIndex(ctx)
        if os.environ.get("PAYER_DIR", "0") == "1":
            import payer_dir
            payer_dir.apply(ctx, self.ix)
        self.stats = defaultdict(int)
        self.why = {}
        # look-ahead: invoices quoted by number on some bank line of the period are settled by that line, not by
        # an anonymous same-amount receipt
        self.quoted = defaultdict(set)
        if LOOKAHEAD:
            for t in ctx.bank.itertuples(index=False):
                if t.amount <= 0:
                    continue
                parts = [t.text, t.counterparty_name, t.remittance_info, t.ref2, t.ref1]
                s = " | ".join(p for p in parts if isinstance(p, str) and p)
                for tk in _tokens(s):
                    if _is_year(tk):
                        continue
                    for iid in self.ix.doc.get((t.entity, tk), ()):
                        self.quoted[iid].add(t.bank_txn_id)

    def exact_names(self, entity, words):
        """Customers whose full core alias appears in the text (word-aligned, no legal-form conflict)."""
        T = " ".join(words)
        offs, p = [], 0
        for w in words:
            offs.append(p)
            p += len(w) + 1
        found = set()
        for k, w in enumerate(words):
            for core, cid, kind, tail in self.ix.alias.get((entity, w), ()):
                p0 = offs[k]
                n = len(core)
                if T[p0:p0 + n] != core or not (p0 + n >= len(T) or T[p0 + n] == " "):
                    continue
                if tail:
                    tt = []
                    for x in T[p0 + n:].split()[:4]:
                        if x in LEGAL or len(x) == 1:
                            tt.append(x)
                        else:
                            break
                    tt = "".join(tt)
                    if tt and tt != tail and not tail.startswith(tt) and not tt.startswith(tail):
                        continue
                found.add(cid)
        return found

    def has_ref(self, ent, toks, items):
        it = self.ix.item
        for tk in toks:
            if (ent, tk) in self.ix.doc or (ent, tk) in self.ix.po:
                return True
            dg = re.sub(r"\D", "", tk).lstrip("0")
            if len(dg) >= 7 and (ent, "#" + dg) in self.ix.doc:
                return True
        # fuzzy: any token whose digits look like one of the customer's document numbers
        for tk in toks:
            draw = re.sub(r"\D", "", tk)
            d = draw.lstrip("0")
            if len(d) < 4:
                continue
            for i in items:
                dg = it[i][12]
                if dg and (d == dg or (len(draw) >= 4 and dg.endswith(draw) and len(draw) >= min(5, len(dg)) - 1)
                           or (len(d) >= 6 and dg.startswith(d))):
                    return True
        return False

    def run_line(self, t):
        self.stats0 = dict(self.stats)
        r = self._run_line(t)
        for k, v in self.stats.items():
            if v != self.stats0.get(k, 0):
                self.why[t.bank_txn_id] = k
        return r

    def _run_line(self, t):
        ent, bdate, A = t.entity, t.booking_date, float(t.amount)
        parts = [t.text, t.counterparty_name, t.remittance_info, t.ref2, t.ref1]
        s = " | ".join(p for p in parts if isinstance(p, str) and p)
        words = [w for w in norm_words(s).split() if w not in CONNECT]
        # (a) payer
        ibans = set(IBAN_RE.findall(s.upper()))
        if isinstance(t.counterparty_account, str) and t.counterparty_account:
            ibans.add(norm(t.counterparty_account))
        by_iban = set()
        for ib in ibans:
            for cid in self.ix.iban.get(norm(ib), ()):
                r = self.ix.cust.get(cid)
                if r is not None and r.entity == ent:
                    by_iban.add(cid)
        by_name = self.exact_names(ent, words)
        if len(by_iban) == 1:
            cid = next(iter(by_iban))
            if by_name and cid not in by_name:
                self.stats["iban_name_conflict"] += 1
                return None
        elif not by_iban and len(by_name) == 1:
            cid = next(iter(by_name))
        else:
            self.stats["no_unique_payer"] += 1
            return None
        it, op = self.ix.item, self.ctx.open
        items = [i for i in self.ix.cust_items.get(cid, ()) if it[i][1] == ent]
        opn = [i for i in items if op.get(i, 0) > 0.005 and it[i][4] <= bdate]
        # (d) open credit notes could combine with invoices -> leave to the general matcher
        crn = [op[i] for i in opn if it[i][0] != "ARI"]
        if crn and any(abs(op[i] - c - A) <= 0.005 for i in opn if it[i][0] == "ARI" for c in crn):
            self.stats["open_credit"] += 1
            return None
        opn = [i for i in opn if it[i][0] == "ARI"]
        # (c) same amount
        same_all = [i for i in opn if it[i][6] == t.currency and abs(op[i] - A) <= 0.005]
        # stale items (issued > HORIZON days before the receipt) are not what a running monthly fee settles
        lo = (_d(bdate) - timedelta(days=HORIZON)).isoformat()
        same = [i for i in same_all if it[i][4] >= lo]
        if LOOKAHEAD and len(same) > 1:
            free = [i for i in same if not (self.quoted.get(i, set()) - {t.bank_txn_id})]
            if free:
                same = free
        if not same or (len(same_all) < 2 and not SINGLE):
            self.stats["lt2"] += 1
            return None
        # (b) [checked after the cheap amount test for speed] no usable reference
        toks = [tk for tk in _tokens(s) if not _is_year(tk)]
        if toks and self.has_ref(ent, toks, items):
            self.stats["has_ref"] += 1
            return None
        month = month_of(words)
        pick = None
        if month and len(same) > 1:
            m, y = month
            cands = []
            for i in same:
                d = it[i][4]
                im, iy = int(d[5:7]), int(d[:4])
                sm, sy = (im - 1, iy) if im > 1 else (12, iy - 1)
                if sm == m and (y is None or y == sy):
                    cands.append(i)
            if not cands:
                self.stats["month_nomatch"] += 1
                return None
            pick = min(cands, key=lambda i: (it[i][4], it[i][5], it[i][3]))
            kind = "month"
        else:
            pick = min(same, key=lambda i: (it[i][4], it[i][5], it[i][3]))
            kind = "fifo" if len(same) > 1 else ("stale" if len(same_all) > 1 else "single")
            if kind == "fifo" and not PLAIN:
                # plain oldest-first tie-break measured below v3 on TRAIN (same-day duplicates are a coin flip and
                # fuzzy references are invisible here) -> leave these lines to the later passes
                self.stats["fifo_skip"] += 1
                return None
        self.stats[kind] += 1
        return [{"item_id": pick, "amount": op[pick], "exception": ""}], kind


def run(ctx):
    f = Fifo(ctx)
    pend = ctx.pending()
    pend = pend[pend.amount > 0]
    # fast pre-filter: the receipt must equal some open AR invoice balance (balances only shrink during this pass)
    amts = {int(round(v * 100)) for i, v in ctx.open.items() if i in f.ix.item and f.ix.item[i][0] == "ARI"}
    for t in pend.itertuples(index=False):
        if ctx.done(t.bank_txn_id) or int(round(float(t.amount) * 100)) not in amts:
            continue
        r = f.run_line(t)
        if r:
            rows, kind = r
            ctx.allocate(t.bank_txn_id, rows, "ar_fifo:" + kind)
    print("   ar_fifo:", dict(f.stats))
    dbg = os.environ.get("FIFO_DEBUG")
    if dbg:
        with open(dbg, "w") as fh:
            fh.write("bank_txn_id,why\n" + "".join(f"{k},{v}\n" for k, v in f.why.items()))
