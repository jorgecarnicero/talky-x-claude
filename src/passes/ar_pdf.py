"""AR receipts whose amount is explained by a clause of the invoice PDF (POLICIES §3, §5.7, §5.8).

Runs before v3import and claims a receipt only when (a) its amount equals what the PDF of an open invoice says is due
now, given the invoice's current open balance, and (b) the line carries evidence of the payer (a reference to the
invoice or to the invoice it replaces, the customer's name or a known IBAN of the customer). Cases:

  instalment plan  : next instalment k of n (cumulative amounts already paid match) -> PARTIAL, last one -> ''
  retention        : untouched invoice paid "amount payable at due date" -> PARTIAL; open == retained part -> ''
  advance          : untouched invoice paid the net due after deducting the proforma advance -> PARTIAL
  replaced invoice : payment of the full replacement total citing the old (cancelled) or new number -> ''

Ambiguous lines (several candidate invoices with equal evidence, or the line references another open invoice of the
customer) are left to the later passes. No labels, no names/ids/amounts are hardcoded.
"""
import os
import re
import sys
from collections import defaultdict
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data import norm, norm_words  # noqa: E402
from ar_index import core_name  # noqa: E402

TOK_RE = re.compile(r"[\s|,;:*\\()+#=/]+")
IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}[A-Z0-9]{10,30})\b")
CUM_TOL = 0.05
WH_COUNTRIES = {"MX", "BR", "MA"}   # POLICIES §3 SHORT_WITHHOLDING
WH_RATES = (0.0125, 0.015, 0.10)
KN_RE = re.compile(r"(?<![\d/])(\d{1,2})\s?/\s?(\d{1,2})(?![\d/])")


def _cents(x):
    return int(round(float(x) * 100))


def _text(t):
    parts = [t.text, t.counterparty_name, t.remittance_info, t.ref1, t.ref2]
    return " | ".join(p for p in parts if isinstance(p, str) and p).upper()


class PdfPass:
    def __init__(self, ctx):
        import pdfinfo as P
        self.ctx = ctx
        self.open = ctx.open
        self.inst = P.instalments(ctx) or {}
        self.ret = P.retention(ctx) or {}
        self.adv = P.advance(ctx) or {}
        repl = P.replaced_by(ctx) or {}
        it = ctx.items
        ar = it[it.item_type.astype(str) == "ARI"]
        self.item = {}
        for iid, ent, pid, dn, isd, cur, tot in zip(ar.item_id, ar.entity.astype(str), ar.party_id, ar.doc_number,
                                                     ar.issue_date, ar.currency.astype(str), ar.total):
            self.item[iid] = (ent, pid, dn or "", isd, cur, float(tot))
        # replacement item -> old doc numbers (norm) it replaces
        self.repl_old = defaultdict(set)
        for k, new in repl.items():
            if isinstance(k, tuple):
                self.repl_old[new].add(k[1])
            elif k in self.item:
                self.repl_old[new].add(norm(self.item[k][2]))
        # cancelled invoices (never settled by bank)
        self.cancelled = {k for k in repl if not isinstance(k, tuple)}
        # amount index: (entity, currency, cents) -> {item}
        self.amt = defaultdict(set)

        def add(iid, x):
            v = self.item.get(iid)
            if v is not None and x:
                self.amt[(v[0], v[4], _cents(x))].add(iid)
        for iid, d in self.inst.items():
            for a in d.get("amounts") or []:
                add(iid, a)
        for iid, d in self.ret.items():
            add(iid, d.get("payable"))
            add(iid, d.get("amount"))
        for iid, d in self.adv.items():
            add(iid, d.get("net_due"))
        self.wh_cust = {r.customer_id for r in ctx.masters["customers"].itertuples(index=False)
                        if r.country in WH_COUNTRIES}
        for iid in self.repl_old:
            if iid in self.item:
                tot = self.item[iid][5]
                add(iid, tot)
                if self.item[iid][1] in self.wh_cust:
                    for r in WH_RATES:
                        add(iid, round(tot - round(tot * r, 2), 2))
        # payer evidence: customer name cores + IBANs
        cu = ctx.masters["customers"]
        self.cores = defaultdict(set)
        self.cust_iban = defaultdict(set)
        for r in cu.itertuples(index=False):
            for nm in (r.name, getattr(r, "trade_name", "")):
                c = core_name(nm) if isinstance(nm, str) else ""
                if len(c) >= 4:
                    self.cores[r.customer_id].add(c)
            if isinstance(r.iban, str) and r.iban:
                self.cust_iban[norm(r.iban)].add(r.customer_id)
        for pid, nm in set(zip(ar.party_id, ar.party_name)):
            c = core_name(nm) if isinstance(nm, str) else ""
            if len(c) >= 4:
                self.cores[pid].add(c)
        P.new_ibans(ctx)
        for ib, pid in (ctx.cache.get("pdfinfo_iban_party") or {}).items():
            self.cust_iban[norm(ib)].add(pid)
        # doc number index of open-ish AR invoices per customer (to detect references to other invoices)
        self.cust_docs = defaultdict(set)
        for iid, v in self.item.items():
            self.cust_docs[(v[0], v[1])].add(iid)
        self.stats = defaultdict(int)
        self.inst_state = {}
        # monthly fees (POLICIES §5.6): open untouched invoices by amount, for lines that name a service month
        self.month = P.service_month(ctx) or {}
        self.fee = defaultdict(list)
        for iid, v in self.item.items():
            o = self.open.get(iid, 0.0)
            if o > 0.005 and abs(o - v[5]) < 0.005:
                self.fee[(v[0], v[4], _cents(o))].append(iid)

    # ------------------------------------------------------------ expectations
    def expected(self, iid):
        """[(cash, exception, kind)] the PDF says is due now for this item, given its open balance."""
        o = self.open.get(iid, 0.0)
        if o <= 0.005 or iid in self.cancelled:
            return []
        tot = self.item[iid][5]
        out = []
        d = self.inst.get(iid)
        if d and d.get("amounts") and abs(sum(d["amounts"]) - tot) <= 0.05:
            am = d["amounts"]
            unpaid = self.unpaid(iid, am, tot - o)
            if unpaid:
                last = len(am) - 1
                for k in unpaid:
                    out.append((am[k], "" if k == last else "PARTIAL", "inst_last" if k == last else "inst", k))
        d = self.ret.get(iid)
        if d and d.get("payable") and d.get("amount"):
            if abs(o - tot) <= 0.01:
                out.append((d["payable"], "PARTIAL", "ret", None))
            elif abs(o - d["amount"]) <= 0.05:
                out.append((o, "", "ret_release", None))
        d = self.adv.get(iid)
        if d and d.get("net_due") and abs(o - tot) <= 0.01 and d["net_due"] < tot - 0.01:
            out.append((d["net_due"], "PARTIAL", "adv", None))
        if iid in self.repl_old and abs(o - tot) <= 0.01:
            out.append((tot, "", "repl", None))
            if self.item[iid][1] in self.wh_cust:
                for r in WH_RATES:
                    out.append((round(tot - round(tot * r, 2), 2), "SHORT_WITHHOLDING", "repl_wht", None))
        return out

    def unpaid(self, iid, am, paid):
        """Indices of the instalments still unpaid. Instalments are not always paid in order (a late first
        instalment can arrive after the second): we track what this pass allocated; otherwise we assume the
        earliest instalments whose amounts add up to what was already paid."""
        st = self.inst_state.get(iid)
        if st and abs(st[0] - paid) <= 0.01:
            return st[1]
        n = len(am)
        res = None
        for c in range(n + 1):
            for comb in combinations(range(n), c):
                if abs(sum(am[k] for k in comb) - paid) <= CUM_TOL:
                    res = [k for k in range(n) if k not in comb]
                    break
            if res is not None or n > 12:
                break
        res = res or []
        self.inst_state[iid] = (paid, res)
        return res

    # ------------------------------------------------------------ evidence
    def evidence(self, iid, text, words, toks, ibans):
        ent, pid, dn = self.item[iid][:3]
        ref = 0
        keys = {norm(dn)} | self.repl_old.get(iid, set())
        digs = {re.sub(r"\D", "", k).lstrip("0") for k in keys}
        for tk in toks:
            if tk in keys:
                ref = 2
                break
            dg = re.sub(r"\D", "", tk).lstrip("0")
            if len(dg) >= 6 and dg in digs:
                ref = 2
                break
        name = 0
        W = " " + words + " "
        for c in self.cores.get(pid, ()):
            if " " + c + " " in W:
                name = 1
                break
            if len(c) >= 12:
                p = c[:10]
                if " " + p in W:
                    name = 1
                    break
        iban = 1 if any(pid in self.cust_iban.get(ib, ()) for ib in ibans) else 0
        return ref, name, iban

    def other_ref(self, iid, toks):
        """True if the text references another invoice of the same customer (not this one / its old number)."""
        ent, pid = self.item[iid][:2]
        tokset = set(toks)
        mine = {norm(self.item[iid][2])} | self.repl_old.get(iid, set())
        for o in self.cust_docs.get((ent, pid), ()):
            if o == iid:
                continue
            n = norm(self.item[o][2])
            if n and n in tokset and n not in mine:
                return True
        return False

    def prep(self, t):
        text = _text(t)
        words = norm_words(text)
        toks = [norm(x) for x in TOK_RE.split(text)]
        toks = [x for x in toks if len(x) >= 4 and any(ch.isdigit() for ch in x)]
        ibans = {norm(x) for x in IBAN_RE.findall(text)}
        if isinstance(t.counterparty_account, str) and t.counterparty_account:
            ibans.add(norm(t.counterparty_account))
        return text, words, toks, ibans

    def run_line(self, t):
        A = float(t.amount)
        c = _cents(A)
        cands = set()
        for dc in (-1, 0, 1):
            cands |= self.amt.get((t.entity, t.currency, c + dc), set())
        if not cands:
            return None
        bdate = t.booking_date
        text, words, toks, ibans = self.prep(t)
        hits = []
        for iid in cands:
            if self.item[iid][3] > bdate:
                continue
            exps = [e for e in self.expected(iid) if abs(e[0] - A) <= 0.011]
            ins = [e for e in exps if e[3] is not None]
            if ins:
                # "PLAZO k/n" in the text names the instalment (they are not always paid in order); else the earliest
                am = self.inst[iid]["amounts"]
                n = len(am)
                pick = None
                for m in KN_RE.finditer(text):
                    kk, nn = int(m.group(1)) - 1, int(m.group(2))
                    if nn == n and 0 <= kk < n and abs(am[kk] - A) <= 0.011:
                        pick = kk
                        break
                if pick is None or not any(e[3] == pick for e in ins):
                    if pick is not None and len(ins) == 1 and pick != ins[0][3]:
                        # ledger and text disagree on which instalment this is: trust the text
                        e = ins[0]
                        ins = [(e[0], "" if pick == n - 1 else "PARTIAL",
                                "inst_last" if pick == n - 1 else "inst", e[3])]
                    pick = min(e[3] for e in ins)
                exps = [e for e in exps if e[3] is None] + [e for e in ins if e[3] == pick]
            for cash, exc, kind, k in exps:
                ref, name, iban = self.evidence(iid, text, words, toks, ibans)
                if not (ref or name or iban):
                    self.stats["noevid_" + kind] += 1
                    continue
                if not ref and self.other_ref(iid, toks):
                    self.stats["otherref_" + kind] += 1
                    continue
                if kind.startswith("repl") and not ref:
                    continue   # a plain full payment is ordinary; ar_match/v3 handle it
                hits.append((ref, name + iban, iid, cash, exc, kind, k))
        if not hits:
            return None
        hits.sort(key=lambda h: (-h[0], -h[1]))
        best = hits[0]
        tied = [h for h in hits if h[0] == best[0] and h[1] == best[1] and h[2] != best[2]]
        if tied:
            # same customer, same plan amounts and no reference: the oldest invoice is paid first (FIFO)
            same_pid = all(self.item[h[2]][1] == self.item[best[2]][1] for h in tied)
            if best[0] or not same_pid:
                self.stats["tie"] += 1
                return None
            grp = [best] + tied
            best = min(grp, key=lambda h: (self.item[h[2]][3], self.item[h[2]][2]))
            self.stats["fifo"] += 1
        ref, ev, iid, cash, exc, kind, k = best
        amt = A if exc == "PARTIAL" else self.open[iid]   # SHORT_WITHHOLDING clears the full open amount
        if k is not None:
            st = self.inst_state[iid]
            self.inst_state[iid] = (round(st[0] + A, 2), [x for x in st[1] if x != k])
            if kind == "inst_last":
                amt = A if self.open[iid] > A + 0.011 else self.open[iid]
        self.stats[kind] += 1
        return [{"item_id": iid, "amount": amt, "exception": exc}], kind


    def month_line(self, t, text, words, toks, ibans):
        """A line naming a service month ("MANT. MAYO 25", "SERVICE AUGUST 2024") pays the invoice of that month
        among several open invoices of the same customer with the same amount."""
        mo = find_month_loose(words.split(), t.booking_date)
        if not mo:
            return None
        A = float(t.amount)
        c = _cents(A)
        bdate = t.booking_date
        cands = [i for dc in (-1, 0, 1) for i in self.fee.get((t.entity, t.currency, c + dc), ())
                 if self.item[i][3] <= bdate and abs(self.open.get(i, 0.0) - self.item[i][5]) < 0.005
                 and abs(self.item[i][5] - A) <= 0.011]
        if not cands:
            return None
        by_pid = defaultdict(list)
        for i in cands:
            by_pid[self.item[i][1]].append(i)
        hits = []
        for pid, lst in by_pid.items():
            ev = self.evidence(lst[0], text, words, toks, ibans)
            if not (ev[1] or ev[2]):
                continue
            for i in lst:
                sm = self.month.get(i)
                if not sm:
                    if len(lst) < 2:
                        continue        # no PDF month and no fixed-fee pattern: leave it
                    y, m = int(self.item[i][3][:4]), int(self.item[i][3][5:7])
                    y, m = (y, m - 1) if m > 1 else (y - 1, 12)
                    sm = f"{y:04d}-{m:02d}"
                if sm == mo:
                    hits.append(i)
        if len(hits) != 1 or self.other_ref(hits[0], toks):
            return None
        i = hits[0]
        self.stats["month"] += 1
        return [{"item_id": i, "amount": self.open[i], "exception": ""}], "month"


MONTH_NAMES = [
    "JANUARY ENERO JANVIER JANUAR GENNAIO JANEIRO STYCZEN JANUARI",
    "FEBRUARY FEBRERO FEVRIER FEBRUAR FEBBRAIO FEVEREIRO LUTY FEBRUARI",
    "MARCH MARZO MARS MARZ MARCO MARZEC MAART",
    "APRIL ABRIL AVRIL APRILE KWIECIEN",
    "MAY MAYO MAI MAGGIO MAIO MAJ MEI",
    "JUNE JUNIO JUIN JUNI GIUGNO JUNHO CZERWIEC",
    "JULY JULIO JUILLET JULI LUGLIO JULHO LIPIEC",
    "AUGUST AGOSTO AOUT SIERPIEN AUGUSTUS",
    "SEPTEMBER SEPTIEMBRE SETIEMBRE SEPTEMBRE SETTEMBRE SETEMBRO WRZESIEN",
    "OCTOBER OCTUBRE OCTOBRE OKTOBER OTTOBRE OUTUBRO PAZDZIERNIK",
    "NOVEMBER NOVIEMBRE NOVEMBRE NOVEMBRO LISTOPAD",
    "DECEMBER DICIEMBRE DECEMBRE DEZEMBER DICEMBRE DEZEMBRO GRUDZIEN",
]
_MN = [(w, i + 1) for i, ws in enumerate(MONTH_NAMES) for w in ws.split()]


def find_month_loose(words, bdate):
    """'YYYY-MM' named in the text (month words may be truncated to >= 4 letters, year optional / truncated).
    Without a year: the latest such month not after the booking month. None if absent or ambiguous."""
    found = set()
    by, bm = int(bdate[:4]), int(bdate[5:7])
    for k, w in enumerate(words):
        if len(w) < 3 or not w.isalpha():
            continue
        ms = {m for full, m in _MN if (full == w) or (len(w) >= 4 and full.startswith(w) and len(full) > 4)}
        if len(ms) != 1:
            continue
        m = ms.pop()
        yr = None
        if k + 1 < len(words):
            nx = words[k + 1]
            if nx.isdigit() and len(nx) == 4 and nx.startswith("20"):
                yr = int(nx)
            elif nx.isdigit() and len(nx) == 2:
                yr = 2000 + int(nx)
        if yr is None:
            yr = by if m <= bm else by - 1
        found.add(f"{yr:04d}-{m:02d}")
    return found.pop() if len(found) == 1 else None


def run(ctx):
    try:
        P = PdfPass(ctx)
    except Exception as e:  # noqa: BLE001  (missing caches / unseen layouts: skip the pass)
        print("   ar_pdf: unavailable:", e)
        return
    pend = ctx.pending()
    pend = pend[pend.amount > 0]
    for t in pend.itertuples(index=False):
        if ctx.done(t.bank_txn_id):
            continue
        r = P.run_line(t)
        if not r:
            r = P.month_line(t, *P.prep(t))
        if r:
            row = r[0][0]
            before = ctx.open.get(row["item_id"], 0.0)
            ctx.allocate(t.bank_txn_id, r[0], "ar_pdf_" + r[1])
            rest = round(before - row["amount"], 2)
            if row["exception"] == "" and rest > 0.011 and r[1] == "inst_last":
                # last instalment paid before an earlier (late) one: the item keeps that balance open
                ctx.open[row["item_id"]] = rest
    print("   ar_pdf:", dict(P.stats))
