"""Indexes for the AR matcher: customer names/IBANs, document numbers, POs, FX rates, payment terms.

Everything is derived from masters + ctx.items (no labels).
"""
import bisect
import re
from collections import defaultdict

from data import norm, norm_words

LEGAL = {
    "SL", "SLU", "SA", "SAU", "SCOOP", "COOP", "SRL", "SNC", "SAS", "SARL", "EURL", "SASU", "LTD", "LIMITED", "LLC",
    "INC", "CORP", "CORPORATION", "CO", "GMBH", "AG", "BV", "NV", "LTDA", "EIRELI", "PTY", "LDA", "PLC", "KG", "OHG",
    "SPZOO", "ZOO", "DE", "CV", "RL", "SC", "UG", "SARLAU", "SPA", "SAPI", "ME", "EPP", "SE", "KFT", "AB", "OY",
    "SPRL", "BVBA", "VOF", "CIA", "LP", "LLP", "PC", "PA", "ULC",
}
# multi-token legal forms after norm_words ("S L", "S P A", "SP Z O O", "S DE R L DE C V" ...)
_SINGLE = set("SLAUPCOVRZDEGMBH")


def strip_legal(words):
    """Drop trailing legal-form tokens (and single letters from dotted forms)."""
    w = list(words)
    while len(w) > 1 and (w[-1] in LEGAL or len(w[-1]) == 1):
        w.pop()
    return w


def legal_tail(words):
    w = list(words)
    tail = []
    while len(w) > 1 and (w[-1] in LEGAL or len(w[-1]) == 1):
        tail.append(w.pop())
    return "".join(reversed(tail))


def core_name(name):
    words = norm_words(name).split()
    # "&" and "Y"/"AND"/"E" connectors are kept as words by banks inconsistently -> drop them
    words = [x for x in words if x not in ("Y", "AND", "ET", "UND", "E", "I")]
    return " ".join(strip_legal(words))


IBAN_RE = re.compile(r"\b([A-Z]{2}\d{2}[A-Z0-9]{10,30})\b")
AMT_RE = re.compile(r"\b\d{1,3}(?:[.,]\d{3})*[.,]\d{2}\b")
SPLIT_RE = re.compile(r"[\s|/,;:*\\()+#=]+")

MONTHS = {}
for i, names in enumerate([
    "JANUARY ENERO JANVIER JANUAR GENNAIO JANEIRO STYCZEN JANUARI JAN ENE GEN",
    "FEBRUARY FEBRERO FEVRIER FEBRUAR FEBBRAIO FEVEREIRO LUTY FEBRUARI FEB FEV",
    "MARCH MARZO MARS MARZ MARCO MARZEC MAART MAR",
    "APRIL ABRIL AVRIL APRILE KWIECIEN APR ABR AVR",
    "MAY MAYO MAI MAGGIO MAIO MAJ MEI MAG",
    "JUNE JUNIO JUIN JUNI GIUGNO JUNHO CZERWIEC JUN GIU",
    "JULY JULIO JUILLET JULI LUGLIO JULHO LIPIEC JUL LUG JUIL",
    "AUGUST AGOSTO AOUT SIERPIEN AUGUSTUS AUG AGO",
    "SEPTEMBER SEPTIEMBRE SETIEMBRE SEPTEMBRE SETTEMBRE SETEMBRO WRZESIEN SEP SEPT SET",
    "OCTOBER OCTUBRE OCTOBRE OKTOBER OTTOBRE OUTUBRO PAZDZIERNIK OCT OKT OTT OUT",
    "NOVEMBER NOVIEMBRE NOVEMBRE NOVEMBRO LISTOPAD NOV",
    "DECEMBER DICIEMBRE DECEMBRE DEZEMBER DICEMBRE DEZEMBRO GRUDZIEN DEC DIC DEZ",
]):
    for n in names.split():
        MONTHS[n] = i + 1


def find_month(words):
    """Return (month, year or None) if the text names a service month."""
    for k, w in enumerate(words):
        m = MONTHS.get(w)
        if m is None:
            continue
        if len(w) <= 3 and w in ("MAR", "MAI", "MAG", "SET", "OUT", "JAN", "DE", "MAY"):
            # ambiguous short tokens: require a neighbouring year or service keyword
            pass
        yr = None
        if k + 1 < len(words):
            nx = words[k + 1]
            if nx.isdigit() and len(nx) == 4 and nx.startswith("20"):
                yr = int(nx)
            elif nx.isdigit() and len(nx) == 2:
                yr = 2000 + int(nx)
        return m, yr
    return None


class FX:
    def __init__(self, df):
        self.r = defaultdict(lambda: ([], []))
        d = df.sort_values("date")
        for cur, g in d.groupby("currency"):
            self.r[cur] = (list(g.date), [float(x) for x in g.units_per_eur])

    def rate(self, cur, date):
        if cur == "EUR":
            return 1.0
        ds, vs = self.r.get(cur, ([], []))
        if not ds:
            return None
        i = bisect.bisect_right(ds, date) - 1
        return vs[max(i, 0)]

    def conv(self, amount, cfrom, cto, date):
        if cfrom == cto:
            return amount
        a, b = self.rate(cfrom, date), self.rate(cto, date)
        if not a or not b:
            return None
        return amount / a * b


class ARIndex:
    def __init__(self, ctx):
        m = ctx.masters
        cu = m["customers"]
        self.cust = {}
        for r in cu.itertuples(index=False):
            self.cust[r.customer_id] = r
        self.ent_country = dict(zip(m["entities"].entity, m["entities"].country))
        # groups
        self.group = defaultdict(set)
        for r in cu.itertuples(index=False):
            if r.group_id:
                self.group[(r.entity, r.group_id)].add(r.customer_id)
        # IBAN -> customers
        self.iban = defaultdict(set)
        for r in cu.itertuples(index=False):
            if r.iban:
                self.iban[norm(r.iban)].add(r.customer_id)
        # payment terms
        self.terms = {}
        for r in m["payment_terms"].itertuples(index=False):
            pct = float(r.early_payment_discount_pct) if r.early_payment_discount_pct else 0.0
            dd = int(r.discount_days) if r.discount_days else 0
            self.terms[r.code] = (pct, dd)
        self.fx = FX(m["fx_rates"])

        # items (AR only)
        it = ctx.items
        ar = it[it.item_type.isin(["ARI", "ARC"])]
        if len(ctx.bank):
            import pandas as pd
            lo = str(pd.Timestamp(ctx.bank.booking_date.min()) - pd.Timedelta(days=400))[:10]
            ar = ar[ar.item_id.isin(set(ctx.open)) | (ar.issue_date >= lo)]
        self.item = {}
        self.cust_items = defaultdict(list)
        self.doc = defaultdict(set)     # (entity, key) -> item ids
        self.po = defaultdict(set)
        names = defaultdict(set)
        dn_s = ar.doc_number.fillna("").astype(str)
        nd_s = dn_s.str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
        dg_s = dn_s.str.replace(r"\D", "", regex=True).str.lstrip("0")
        po_s = ar.po.fillna("").astype(str).str.upper().str.replace(r"[^A-Z0-9]", "", regex=True)
        for iid, typ, ent, pid, dn, isd, dud, cur, tot, pt, po, rel, nd, dg, npo, rsn in zip(
                ar.item_id, ar.item_type.astype(str), ar.entity, ar.party_id, dn_s, ar.issue_date,
                ar.due_date, ar.currency.astype(str), ar.total, ar.payment_terms.astype(str), ar.po,
                ar.related_document, nd_s, dg_s, po_s, ar.reason.astype(str)):
            self.item[iid] = (typ, ent, pid, dn, isd, dud, cur, float(tot), pt if pt != "nan" else "",
                              po or "", rel or "", nd, dg, npo,
                              "C" if rsn.startswith("Anul") else ("R" if rsn.startswith("Rappel") else ""))
            self.cust_items[pid].append(iid)
            if nd:
                self.doc[(ent, nd)].add(iid)
            if len(dg) >= 6:
                self.doc[(ent, "#" + dg)].add(iid)
            if len(npo) >= 6:
                self.po[(ent, npo)].add(iid)
        for pid, pname in set(zip(ar.party_id, ar.party_name)):
            if pname:
                names[pid].add(pname)
        for pid in self.cust_items:
            self.cust_items[pid].sort(key=lambda i: (self.item[i][4], self.item[i][3]))
        self.doc_by_num = {}
        for iid, v in self.item.items():
            if v[3]:
                self.doc_by_num[(v[1], v[3])] = iid
        # name aliases: master name, trade name, names printed on invoices
        self.alias = defaultdict(list)   # (entity, first word) -> [(core, cust_id, kind)]
        seen = set()
        for cid, r in self.cust.items():
            al = [(r.name, 0), (r.trade_name, 1)] + [(n, 2) for n in names.get(cid, ())]
            for a, kind in al:
                c = core_name(a) if isinstance(a, str) else ""
                if len(c) < 3:
                    continue
                k = (r.entity, c, cid)
                if k in seen:
                    continue
                seen.add(k)
                self.alias[(r.entity, c.split()[0])].append((c, cid, kind, legal_tail(norm_words(a).split())))

    def add_alias(self, entity, core, cid):
        if len(core) < 5:
            return
        key = (entity, core.split()[0])
        lst = self.alias[key]
        for c, x, _, _ in lst:
            if c == core and x == cid:
                return
        lst.append((core, cid, 3, ""))
