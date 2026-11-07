"""Bank reconciliation v1 — cascade of deterministic matchers.

    python solution/bankrec.py --phase sample --out sub_sample_v1.csv.gz
    python solution/bankrec.py --phase live   --out sub_live_v1.csv.gz

Passes (each bank line is resolved by the first pass that claims it):
  1. batch files: SEPA direct-debit batches, card settlements, cheque deposits, payment runs (transfers / confirming)
  2. direct-debit returns (mandate id in text)
  3. remittance advices (csv / txt / xlsx / pdf)
  4. document references in bank text (+ discount / partial logic)
  5. payer identification (IBAN / name) + amount matching over its open items
  6. category classifier learned from TRAIN golden for whatever is left
Standard library only (+ `pdftotext` on PATH for PDF advices).
"""
import argparse
import csv
import gzip
import io
import json
import math
import os
import pickle
import re
import subprocess
import sys
import time
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from itertools import combinations

csv.field_size_limit(10 ** 8)
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:7.1f}s]", *a, flush=True)


# ----------------------------------------------------------------------------- helpers
def rcsv(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8", newline="") as fh:
        yield from csv.DictReader(fh)


def cents(s):
    s = (s or "").strip()
    return int(round(float(s) * 100)) if s else 0


def d(s):
    return date.fromisoformat(s[:10]) if s else None


def strip_acc(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def nalnum(s):
    return re.sub(r"[^A-Z0-9]", "", strip_acc(s).upper())


def digits(s):
    return re.sub(r"\D", "", s or "")


STOP = set("""SL SA SAS SARL EURL SLU SAU SCOOP COOP GMBH CO KG EK AG LTD LDA SNC SRL SPA BV NV INC LLC PLC SP ZOO
DE DEL LA LAS LOS EL Y E ET DU DES LE LES UND THE AND OF FOR A EN AU PAR TRANSFERENCIA TRANSFER SEPA PAGO FRA FRAS
FACTURA FACTURAS INMEDIATA FAVOR SU VIR RECU MOTIF REF REMI ORDP EREF SVWZ UEBERWEISUNG PAYMENT CONCEPTO ORDENANTE
RECHNUNG FACTURE INVOICE GROUP GRUPO""".split())


def name_tokens(s):
    return [t for t in re.split(r"[^A-Z0-9]+", strip_acc(s).upper()) if len(t) >= 2 and t not in STOP and not t.isdigit()]


AMT_RE = re.compile(r"-?\d{1,3}(?:[.,\s]\d{3})*[.,]\d{2}(?!\d)|-?\d+[.,]\d{2}(?!\d)")


def parse_amount(s):
    s = s.strip().replace(" ", "")
    neg = s.startswith("-")
    s = s.lstrip("-")
    if re.search(r",\d{2}$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        v = int(round(float(s) * 100))
    except ValueError:
        return None
    return -v if neg else v


USE_PDFS = os.environ.get("BANKREC_NO_PDFS") != "1"
UNIDENTIFIED_ONLY_UNKNOWN_PAYER = os.environ.get("BANKREC_UNID_KNOWN") == "1"  # A/B: dev says always flag (81.65 vs 81.35)
EXC_FULL = {"", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "OVERPAYMENT"}
WITHHOLDING_ENTITIES = {"MX01", "BR01", "MA01"}  # POLICIES §3: some customers in Mexico, Brazil and Morocco
SEPA_COUNTRIES = set("AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK IS LI NO CH MC SM "
                     "AD VA GB GI".split())

_MONTHS = {}
for _i, _names in enumerate([
        "ENERO JANUARY JANVIER JANUAR JANEIRO GENNAIO STYCZEN JAN ENE",
        "FEBRERO FEBRUARY FEVRIER FEBRUAR FEVEREIRO FEBBRAIO LUTY FEB FEV",
        "MARZO MARCH MARS MAERZ MARZ MARCO MARZEC MAR",
        "ABRIL APRIL AVRIL APRILE KWIECIEN APR ABR AVR",
        "MAYO MAY MAI MAIO MAGGIO MAJ",
        "JUNIO JUNE JUIN JUNI JUNHO GIUGNO CZERWIEC JUN",
        "JULIO JULY JUILLET JULI JULHO LUGLIO LIPIEC JUL",
        "AGOSTO AUGUST AOUT AGOSTO SIERPIEN AUG AGO",
        "SEPTIEMBRE SEPTEMBER SEPTEMBRE SETEMBRO SETTEMBRE WRZESIEN SEP SEPT SET",
        "OCTUBRE OCTOBER OCTOBRE OKTOBER OUTUBRO OTTOBRE PAZDZIERNIK OCT OKT OUT",
        "NOVIEMBRE NOVEMBER NOVEMBRE NOVEMBRO LISTOPAD NOV",
        "DICIEMBRE DECEMBER DECEMBRE DEZEMBER DEZEMBRO DICEMBRE GRUDZIEN DEC DIC DEZ"], 1):
    for _n in _names.split():
        _MONTHS[_n] = _i
_MONTH_RE = re.compile(r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s*'?(\d{2,4})?\b")


_KN_RE = re.compile(r"(?:INSTAL+MENT|PLAZO|ECHEANCE|ECHEANCES|RATA|RATE|PARCELA|VENCIMIENTO|TEILZAHLUNG|CUOTA|"
                    r"TERMIN|RATY|PAYMENT)\s*(?:NO\.?\s*)?(\d{1,2})\s*(?:/|OF|DE|SUR|VON|DI|Z)\s*(\d{1,2})\b")


def instalment_k_of_n(text):
    """"PLAZO 2/3" / "INSTALMENT 3 OF 3" / "ECHEANCE 1/3" -> (k, n)"""
    m = _KN_RE.search(strip_acc(text).upper())
    if m:
        k, n = int(m.group(1)), int(m.group(2))
        if 1 <= k <= n <= 12 and n >= 2:
            return k, n
    return None


def service_month(text, bday):
    """("MANT. MAYO 25" / "SERVICE APRIL 2025") -> (year, month) of the service, else None"""
    s = strip_acc(text).upper()
    # only "service month" wording ("MANT. MAYO 25", "SERVICE APRIL 2025"); "PAGO JUNIO" is not a service month
    kw = re.search(r"\b(MANT\w*|SERVIC\w*|SERVIZ\w*|WARTUNG|ENTRETIEN|MAINT\w*|CUOTA|FEE|MENSUALIDAD|USLUG\w*|"
                   r"PRESTATION\w*|LEISTUNG\w*|MONTHLY)\b\.?", s)
    if not kw:
        return None
    s = s[kw.start():]
    for m in _MONTH_RE.finditer(s):
        mon = _MONTHS[m.group(1)]
        yy = m.group(2)
        if yy:
            y = int(yy) + (2000 if len(yy) == 2 else 0)
        else:
            y = bday.year if mon <= bday.month + 1 else bday.year - 1
        if 2018 <= y <= 2030:
            return y, mon
    return None


# ----------------------------------------------------------------------------- items
class Item:
    __slots__ = ("id", "typ", "side", "entity", "party", "name", "doc", "cur", "total", "open", "issue", "due", "terms",
                 "po", "related", "pm", "paid_by",
                 # from the document PDF (solution/pdfs.py)
                 "plan", "payable", "adv_net", "smonth", "cn_mode", "cancelled", "replaced_by", "factor")

    def __init__(self, **k):
        for s in self.__slots__:
            setattr(self, s, k.get(s))

    def __getattr__(self, name):  # slots added after a cache was pickled read as None
        if name in Item.__slots__:
            return None
        raise AttributeError(name)


def load_items(root, phase):
    """Return dict item_id -> Item with open amounts at phase start."""
    items = {}

    def add_files(folder, only=None, open_amt=None, pred=None):
        for r in rcsv(os.path.join(folder, "ar_invoices.csv.gz")):
            if only is not None and r["item_id"] not in only:
                continue
            if pred is not None and not pred(r):
                continue
            items[r["item_id"]] = Item(id=r["item_id"], typ="ARI", side="AR", entity=r["entity"], party=r["customer_id"],
                                       name=r["customer_name"], doc=r["document_number"], cur=r["currency"],
                                       total=cents(r["total_amount"]), issue=d(r["issue_date"]), due=d(r["due_date"]),
                                       terms=r["payment_terms"], po=r["customer_po"], related="", pm=r["payment_method"])
        for r in rcsv(os.path.join(folder, "ap_invoices.csv.gz")):
            if only is not None and r["item_id"] not in only:
                continue
            if pred is not None and not pred(r):
                continue
            items[r["item_id"]] = Item(id=r["item_id"], typ="API", side="AP", entity=r["entity"], party=r["supplier_id"],
                                       name=r["supplier_name"], doc=r["supplier_invoice_number"], cur=r["currency"],
                                       total=cents(r["total_amount"]), issue=d(r["issue_date"]), due=d(r["due_date"]),
                                       terms=r["payment_terms"], po=r["our_po"], related="", pm=r["payment_method"])
        for r in rcsv(os.path.join(folder, "credit_notes.csv.gz")):
            if only is not None and r["item_id"] not in only:
                continue
            if pred is not None and not pred(r):
                continue
            items[r["item_id"]] = Item(id=r["item_id"], typ=r["side"] + "C", side=r["side"], entity=r["entity"],
                                       party=r["party_id"], name=r["party_name"], doc=r["document_number"],
                                       cur=r["currency"], total=cents(r["total_amount"]), issue=d(r["issue_date"]),
                                       due=d(r["issue_date"]), terms="", po="", related=r["related_document"], pm="")

    if phase == "dev":
        return load_items_dev(root, add_files, items)
    if phase in ("sample", "train"):
        add_files(os.path.join(root, phase))
        for it in items.values():
            it.open = it.total
    else:
        prev = {"live": "train", "final": "live"}[phase]
        cache = os.path.join(root, "solution", "cache", f"open_start_{phase}_v2.pkl")
        oi = {r["item_id"]: r for r in rcsv(os.path.join(root, phase, "open_items_at_start.csv.gz"))}
        if os.path.exists(cache):
            with open(cache, "rb") as fh:
                items.update(pickle.load(fh))
        else:
            # enrich carried-over open items with full details from earlier phases, and keep the items of the last
            # ~200 days that were already settled (open = 0) so that a second payment is seen as DUPLICATE_PAYMENT
            start = {"live": "2025-07-01", "final": "2026-01-01"}[phase]
            cut = (d(start) - timedelta(days=200)).isoformat()
            oi_ids = set(oi)

            def pred(r):
                return r["item_id"] in oi_ids or r["issue_date"] >= cut
            for ph in (["train"] if prev == "train" else ["train", "live"]):
                add_files(os.path.join(root, ph), pred=pred)
            for iid, it in items.items():
                if iid not in oi_ids:
                    it.open = 0
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "wb") as fh:
                pickle.dump(dict(items), fh)
        for iid, r in oi.items():
            it = items.get(iid)
            if it is None:  # minimal record
                typ = r["item_type"]
                it = items[iid] = Item(id=iid, typ=typ, side=typ[:2], entity=r["entity"], party=r["party_id"], name="",
                                       doc=r["document_number"], cur=r["currency"], total=cents(r["total_amount"]),
                                       issue=d(r["issue_date"]), due=d(r["due_date"]), terms="", po="", related="", pm="")
            it.open = cents(r["open_amount"])
        for it in items.values():
            if it.open is None:
                it.open = 0
        n0 = len(items)
        add_files(os.path.join(root, phase))
        for it in items.values():
            if it.open is None:
                it.open = it.total
        log(f"items: {len(oi)} carried over + {len(items) - n0 + 0} new")
    return items


DEV_MONTHS = ["202504", "202505"]  # held-out TRAIN months (overridden by --months)


def dev_bounds():
    lo = DEV_MONTHS[0]
    hi = DEV_MONTHS[-1]
    start = date(int(lo[:4]), int(lo[4:]), 1)
    y, m = int(hi[:4]), int(hi[4:]) + 1
    if m == 13:
        y, m = y + 1, 1
    end = date(y, m, 1) - timedelta(days=1)
    return start, end


def load_items_dev(root, add_files, items):
    """Open items at the start of the held-out window, rebuilt from the TRAIN golden; plus items issued in the window.
    Items fully settled in the 120 days before the window are kept with open=0 (for duplicate detection)."""
    start, end = dev_bounds()
    cache = os.path.join(root, "solution", "cache", f"dev_items_{DEV_MONTHS[0]}_{DEV_MONTHS[-1]}.pkl")
    if os.path.exists(cache):
        with open(cache, "rb") as fh:
            items.update(pickle.load(fh))
        log(f"dev items from cache: {len(items)}")
        return items
    tr = os.path.join(root, "train")
    before = {}
    for t in rcsv(os.path.join(tr, "bank_transactions.csv.gz")):
        bd = d(t["booking_date"])
        if bd < start:
            before[t["bank_txn_id"]] = bd
    paid = defaultdict(int)
    last_paid = {}
    for r in rcsv(os.path.join(tr, "golden_allocations.csv.gz")):
        bd = before.get(r["bank_txn_id"])
        if bd is None or not r["item_id"]:
            continue
        a = cents(r["amount"])
        if r["exception"] == "RETURNED":
            paid[r["item_id"]] -= a
        elif r["exception"] == "DUPLICATE_PAYMENT":
            continue
        else:
            paid[r["item_id"]] += a
            last_paid[r["item_id"]] = max(bd, last_paid.get(r["item_id"], bd))
    recent = start - timedelta(days=120)
    end_s = end.isoformat()

    def pred(r):
        if r["issue_date"] > end_s:
            return False
        iid = r["item_id"]
        if cents(r["total_amount"]) - paid.get(iid, 0) > 0:
            return True
        lp = last_paid.get(iid)
        return lp is not None and lp >= recent

    add_files(tr, pred=pred)
    keep = {}
    for iid, it in items.items():
        if it.issue and it.issue > end:
            continue
        it.open = max(0, it.total - paid.get(iid, 0))
        if it.open > 0 or (last_paid.get(iid) and last_paid[iid] >= recent):
            keep[iid] = it
    items.clear()
    items.update(keep)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "wb") as fh:
        pickle.dump(keep, fh)
    log(f"dev items: {len(items)} (open at {start} or settled recently)")
    return items


# ----------------------------------------------------------------------------- matcher state
class Rec:
    def __init__(self, root, phase):
        self.root, self.phase = root, phase
        self.items = load_items(root, phase)
        log(f"{len(self.items)} items loaded")
        self.payer_known = set()
        self.settled_by_row = {}  # cheque / direct-debit receipt -> item it settled (for later returns)
        self.out = {}  # bank_txn_id -> list of rows (item_id, amount_cents, category, exception)
        self.why = {}  # bank_txn_id -> matcher name
        self._index_items()
        self._load_masters()
        if USE_PDFS:
            self._apply_pdfs()

    # --------------------------------------------------------------- indexes
    def _index_items(self):
        self.by_doc = defaultdict(list)  # normalized doc -> items
        self.by_digits = defaultdict(list)  # (side, entity, digits-suffix) -> items
        self.by_party = defaultdict(list)
        for it in self.items.values():
            nd = nalnum(it.doc)
            self.by_doc[nd].append(it)
            self.by_party[it.party].append(it)
            dg = digits(it.doc)
            if it.side == "AR" and len(dg) >= 5:
                # our numbers: FV25-0405001 -> 250405001, 0405001, 405001
                for k in {dg, dg[2:], dg[2:].lstrip("0"), dg[-6:], dg[-7:]}:
                    if len(k) >= 5:
                        self.by_digits[(it.entity, k)].append(it)
            if it.po and it.side == "AR":
                self.by_doc["PO:" + nalnum(it.po)].append(it)
            if it.typ == "APC" and it.related:
                self.by_doc["REL:" + nalnum(it.related)].append(it)
        for lst in self.by_party.values():
            lst.sort(key=lambda x: (x.issue or date.min, x.id))

    def _load_masters(self):
        m = os.path.join(self.root, "masters")
        if self.phase == "final" and os.path.isdir(os.path.join(self.root, "final", "masters_final")):
            m = os.path.join(self.root, "final", "masters_final")
        self.accounts = {r["account_id"]: r for r in rcsv(os.path.join(m, "bank_accounts.csv"))}
        self.terms = {r["code"]: r for r in rcsv(os.path.join(m, "payment_terms.csv"))}
        self.customers = {r["customer_id"]: r for r in rcsv(os.path.join(m, "customers.csv"))}
        self.suppliers = {r["supplier_id"]: r for r in rcsv(os.path.join(m, "suppliers.csv"))}
        self.fx = defaultdict(dict)
        for r in rcsv(os.path.join(m, "fx_rates.csv")):
            self.fx[r["currency"]][r["date"]] = float(r["units_per_eur"])
        self.party_by_iban = defaultdict(set)
        self.party_by_mandate = {}
        self.group_members = defaultdict(set)
        for c in self.customers.values():
            if c["iban"]:
                self.party_by_iban[c["iban"]].add(c["customer_id"])
            if c["sepa_mandate_id"]:
                self.party_by_mandate[c["sepa_mandate_id"]] = c["customer_id"]
            if c["group_id"]:
                self.group_members[c["group_id"]].add(c["customer_id"])
        for s in self.suppliers.values():
            if s["iban"]:
                self.party_by_iban[s["iban"]].add(s["supplier_id"])
        # name index per entity: token -> set(party)
        self.name_idx = defaultdict(lambda: defaultdict(set))
        self.party_tokens = defaultdict(set)
        self.party_entity = {}
        for c in self.customers.values():
            self.party_entity[c["customer_id"]] = c["entity"]
            for nm in (c["name"], c["trade_name"]):
                self.party_tokens[c["customer_id"]].update(name_tokens(nm))
        for s in self.suppliers.values():
            self.party_entity[s["supplier_id"]] = s["entity"]
            self.party_tokens[s["supplier_id"]].update(name_tokens(s["name"]))
        for it in self.items.values():  # historical names printed on documents
            if it.name and it.party:
                self.party_tokens[it.party].update(name_tokens(it.name))
                self.party_entity.setdefault(it.party, it.entity)
        df = Counter()
        for p, toks in self.party_tokens.items():
            ent = self.party_entity.get(p)
            for t in toks:
                self.name_idx[ent][t].add(p)
                df[t] += 1
        n = max(1, len(self.party_tokens))
        self.idf = {t: math.log(n / c) for t, c in df.items()}
        self.party_tot = {p: (sum(self.idf.get(x, 0) for x in sorted(toks)) or 1.0) for p, toks in self.party_tokens.items()}
        log(f"masters: {len(self.customers)} customers, {len(self.suppliers)} suppliers")

    # --------------------------------------------------------------- document PDFs
    def _apply_pdfs(self):
        try:
            sys.path.insert(0, os.path.join(self.root, "solution"))
            import pdfs  # noqa
            idx = pdfs.load_index(self.root, build=True)
        except ImportError as e:
            log(f"PDFs skipped ({e}); pip install pymupdf")
            return
        typ_of = {"invoice": "ARI", "credit_note": "ARC", "supplier_invoice": "API", "supplier_credit_note": "APC"}
        n = Counter()
        cancels = []
        for (ph, f), v in idx.items():
            cl = v["clauses"]
            if not cl:
                continue
            if "new_iban" in cl and v["party_id"]:
                self.party_by_iban[cl["new_iban"]["iban"]].add(v["party_id"])
                n["new_iban"] += 1
            typ = typ_of.get(v["document_type"])
            if not typ:
                continue
            its = [i for i in self.by_doc.get(nalnum(v["document_number"]), []) if i.typ == typ and i.entity == v["entity"]]
            if len(its) > 1 and v["party_id"]:
                its = [i for i in its if i.party == v["party_id"]] or its
            if not its:
                continue
            it = its[0]
            ins = cl.get("instalments")
            if ins and ins["amounts"] and (ins["currency"] in (None, it.cur)):
                it.plan = ins["amounts"]
                n["instalments"] += 1
            if "retention" in cl:
                it.payable = cl["retention"]["payable"]
                n["retention"] += 1
            if "advance" in cl:
                it.adv_net = cl["advance"]["net_due"]
                n["advance"] += 1
            if "service_month" in cl:
                y, m = cl["service_month"].split("-")
                it.smonth = (int(y), int(m))
                n["service_month"] += 1
            if "rebate" in cl:
                it.cn_mode = cl["rebate"]["mode"]
                n["rebate_" + it.cn_mode] += 1
            if "factor" in cl:
                it.factor = cl["factor"]
                n["factor"] += 1
            if "cn_mode" in cl:
                it.cn_mode = cl["cn_mode"]
                n["apc_" + it.cn_mode] += 1
            if "replaces" in cl:
                cancels.append((it, cl["replaces"]["document_number"]))
        # "anula y sustituye": the old invoice and its cancelling credit note are never settled by bank (POLICIES §5.7)
        for new, old_doc in cancels:
            olds = [i for i in self.by_doc.get(nalnum(old_doc), []) if i.typ == "ARI" and i.entity == new.entity
                    and i.id != new.id]
            for old in olds:
                old.cancelled, old.replaced_by, old.open = True, new, 0
                n["replaced"] += 1
                for cn in self.by_party.get(old.party, ()):
                    if cn.typ == "ARC" and nalnum(cn.related) == nalnum(old.doc):
                        cn.cancelled, cn.open = True, 0
                        n["cancel_cn"] += 1
        log("pdf clauses applied:", dict(n))

    # --------------------------------------------------------------- utilities
    def fx_rate(self, cur, day):
        if cur == "EUR":
            return 1.0
        tab = self.fx.get(cur)
        if not tab:
            return None
        for i in range(10):
            k = (day - timedelta(days=i)).isoformat()
            if k in tab:
                return tab[k]
        return None

    def convert(self, amt, from_cur, to_cur, day):
        a, b = self.fx_rate(from_cur, day), self.fx_rate(to_cur, day)
        if a is None or b is None:
            return None
        return amt / a * b

    def assign(self, t, rows, why):
        """rows: list of (item, amount_cents, exception)"""
        out = []
        for it, amt, exc in rows:
            out.append((it.id, abs(amt), "", exc))
            if exc == "RETURNED":
                it.open += abs(amt)
            elif exc == "DUPLICATE_PAYMENT":
                pass
            else:
                it.open = max(0, it.open - abs(amt))
        # FX flag: line currency differs from item currency
        fixed = []
        for (iid, amt, cat, exc) in out:
            it = self.items[iid]
            if it.cur != t["currency"] and it.typ in ("ARI", "API") and exc in EXC_FULL:
                exc = "FX_DIFFERENCE"
            fixed.append((iid, amt, cat, exc))
        # merge duplicate item ids
        merged = {}
        for r in fixed:
            if r[0] in merged:
                o = merged[r[0]]
                merged[r[0]] = (o[0], o[1] + r[1], o[2], o[3] or r[3])
            else:
                merged[r[0]] = r
        self.out[t["bank_txn_id"]] = list(merged.values())
        self.why[t["bank_txn_id"]] = why

    def category(self, t, cat, exc="", why="cat"):
        self.out[t["bank_txn_id"]] = [("", 0, cat, exc)]
        self.why[t["bank_txn_id"]] = why

    def find_doc(self, doc, side=None, entity=None, party=None, open_only=False):
        c = self.by_doc.get(nalnum(doc), [])
        if side:
            c = [i for i in c if i.side == side]
        if entity and len(c) > 1:
            c2 = [i for i in c if i.entity == entity]
            c = c2 or c
        if party and len(c) > 1:
            c2 = [i for i in c if i.party == party]
            c = c2 or c
        if open_only:
            c = [i for i in c if i.open > 0]
        return c

    # --------------------------------------------------------------- party identification
    def identify_party(self, t, side="AR"):
        """Return list of (party, score) best first."""
        ent = t["entity"]
        cands = Counter()
        ib = t["counterparty_account"] or ""
        for iban in [ib] + re.findall(r"\b[A-Z]{2}\d{2}[A-Z0-9]{12,30}\b", t["text"]):
            for p in sorted(self.party_by_iban.get(iban, ())):
                cands[p] += 5.0
        txt = " ".join([t["text"], t["counterparty_name"], t["ref2"]])
        toks = set(name_tokens(txt))
        idx = self.name_idx.get(ent, {})
        # candidate generation from the rarest tokens, scoring on all tokens of the candidate
        ranked = sorted((len(idx[tk]), tk) for tk in toks if tk in idx)
        pool = set()
        for n_, tk in ranked[:4]:
            if n_ <= 1500 and len(pool) + n_ <= 2500:
                pool |= idx[tk]
        ptot = self.party_tot
        for p in sorted(pool):
            pt = self.party_tokens[p]
            tot = ptot[p]
            m = sum(self.idf.get(x, 0) for x in sorted(pt & toks))
            frac = m / tot
            if frac >= 0.5:
                cands[p] += frac + m / 1000.0
        pref = "C" if side == "AR" else "S"
        res = [(p, s) for p, s in cands.most_common() if p.startswith(pref)]
        return res

    def open_items_of(self, parties, side, cur=None):
        out = []
        seen = set()
        for p in parties:
            for it in self.by_party.get(p, ()):
                if it.side == side and it.open > 0 and it.id not in seen:
                    seen.add(it.id)
                    out.append(it)
        out.sort(key=lambda x: (x.issue or date.min, x.id))
        return out

    def discount_amount(self, it, bday):
        """cash expected if early-payment discount is taken within terms; None otherwise"""
        tr = self.terms.get(it.terms or "")
        if not tr or not tr["early_payment_discount_pct"] or it.typ != "ARI":
            return None
        if it.open != it.total:
            return None
        pct = float(tr["early_payment_discount_pct"])
        lim = it.issue + timedelta(days=int(tr["discount_days"]) + 3)
        disc = int(round(it.total * pct / 100))
        return it.total - disc, bday <= lim

    # ============================================================== pass 1: batch files
    PHASE_RANGE = {"sample": ("202506", "202506"), "train": ("202107", "202506"), "live": ("202507", "202512"),
                   "final": ("202601", "202606")}

    def month_window(self, before=3, after=1):
        lo, hi = (DEV_MONTHS[0], DEV_MONTHS[-1]) if self.phase == "dev" else self.PHASE_RANGE[self.phase]

        def shift(ym, k):
            y, m = int(ym[:4]), int(ym[4:]) + k
            while m < 1:
                y, m = y - 1, m + 12
            while m > 12:
                y, m = y + 1, m - 12
            return f"{y}{m:02d}"
        return shift(lo, -before), shift(hi, after)

    def data_folders(self):
        """every phase folder present (sample is a subset of train: use train/live around it when available)"""
        fs = [f for f in ("train", "live", "final") if os.path.isdir(os.path.join(self.root, f))]
        return fs or [self.phase]

    def aux_files(self, sub):
        """batch / payment files in a window of months around the phase (confirming runs are created months before
        the debits; advices arrive after the cash)."""
        lo, hi = self.month_window()
        files, seen = [], set()
        for f in self.data_folders():
            p = os.path.join(self.root, f, sub)
            if not os.path.isdir(p):
                continue
            for n in os.listdir(p):
                if n.startswith("._") or not n.endswith(".csv") or n in seen:
                    continue
                m = re.search(r"_(\d{6})", n)
                if m and lo <= m.group(1) <= hi:
                    seen.add(n)
                    files.append(os.path.join(p, n))
        return files

    def pass_batches(self, txns):
        # ---- direct-debit batches
        dd = defaultdict(list)
        for f in self.aux_files("direct_debit_batches"):
            for r in rcsv(f):
                dd[r["batch_id"]].append(r)
        self.dd_rows = dd
        dd_by_amt = defaultdict(list)
        for bid, rows in dd.items():
            dd_by_amt[(rows[0]["account_id"], cents(rows[0]["batch_total"]))].append(bid)
        # ---- card settlements
        cs = defaultdict(list)
        for f in self.aux_files("card_settlements"):
            for r in rcsv(f):
                cs[r["settlement_id"]].append(r)
        cs_by_amt = defaultdict(list)
        for sid, rows in cs.items():
            cs_by_amt[(rows[0]["account_id"], sum(cents(x["net"]) for x in rows))].append(sid)
        # ---- cheque deposits
        cq = defaultdict(list)
        for f in self.aux_files("cheque_deposits"):
            for r in rcsv(f):
                cq[r["deposit_id"]].append(r)
        cq_by_amt = defaultdict(list)
        for did, rows in cq.items():
            cq_by_amt[(rows[0]["account_id"], cents(rows[0]["deposit_total"]))].append(did)
        # ---- payment runs
        pr_e2e = defaultdict(list)
        pr_run = defaultdict(list)
        for f in self.aux_files("payment_runs"):
            for r in rcsv(f):
                pr_e2e[r["end_to_end_id"]].append(r)
                pr_run[r["run_id"]].append(r)
        pr_by_amt = defaultdict(list)
        for e2e, rows in pr_e2e.items():
            r0 = rows[0]
            if r0["run_type"] == "transfers":
                pr_by_amt[(r0["debtor_account_id"], cents(r0["transfer_amount"]))].append(e2e)
            elif r0["run_type"] == "aggregated":
                pass
        agg_by_amt = defaultdict(list)
        for rid, rows in pr_run.items():
            if rows[0]["run_type"] == "aggregated":
                tot = sum(cents(x["document_amount"]) for x in rows)
                agg_by_amt[(rows[0]["debtor_account_id"], tot)].append(rid)
        self.cs_rows, self.cq_rows, self.pr_e2e = cs, cq, pr_e2e
        log(f"aux: {len(dd)} dd batches, {len(cs)} card settl., {len(cq)} cheque dep., {len(pr_run)} payment runs")

        used_batch = set()
        n = Counter()
        for t in txns:
            amt = cents(t["amount"])
            acc = t["account_id"]
            blob = " ".join([t["text"], t["ref1"], t["ref2"], t["end_to_end_id"], t["remittance_info"]])
            bday = d(t["booking_date"])
            if amt > 0:
                # DD batch
                bid = None
                for tok in re.findall(r"\d{11}", blob):
                    if tok in dd and tok not in used_batch:
                        bid = tok
                        break
                if not bid:
                    c = [b for b in dd_by_amt.get((acc, amt), []) if b not in used_batch]
                    if c:
                        c.sort(key=lambda b: abs((d(dd[b][0]["collection_date"]) - bday).days))
                        if abs((d(dd[c[0]][0]["collection_date"]) - bday).days) <= 5:
                            bid = c[0]
                if bid:
                    used_batch.add(bid)
                    rows = []
                    for r in dd[bid]:
                        it = self.dd_item(r, t["entity"])
                        if it:
                            rows.append((it, cents(r["amount"]), ""))
                            self.settled_by_row[("DD", r["end_to_end_id"])] = it
                    if rows:
                        self.assign(t, rows, "dd_batch")
                        n["dd_batch"] += 1
                        continue
                # card settlement
                c = [s for s in cs_by_amt.get((acc, amt), []) if s not in used_batch]
                if c:
                    c.sort(key=lambda s: abs((d(cs[s][0]["settlement_date"]) - bday).days))
                    sid = c[0]
                    if abs((d(cs[sid][0]["settlement_date"]) - bday).days) <= 5:
                        used_batch.add(sid)
                        rows = []
                        for r in cs[sid]:
                            its = self.find_doc(r["order_number"], side="AR", entity=t["entity"])
                            if its:
                                rows.append((its[0], cents(r["gross"]), ""))
                        if rows:
                            self.assign(t, rows, "card")
                            n["card"] += 1
                            continue
                # cheque deposit
                did = None
                for tok in re.findall(r"[A-Z]{2,4}\d{8,}", blob):
                    if tok in cq and tok not in used_batch:
                        did = tok
                        break
                if not did:
                    c = [x for x in cq_by_amt.get((acc, amt), []) if x not in used_batch]
                    if c:
                        c.sort(key=lambda x: abs((d(cq[x][0]["deposit_date"]) - bday).days))
                        if abs((d(cq[c[0]][0]["deposit_date"]) - bday).days) <= 5:
                            did = c[0]
                if did:
                    used_batch.add(did)
                    rows = []
                    for r in cq[did]:
                        x = self.cheque_item(r, t["entity"])
                        if x:
                            rows.append(x)
                            self.settled_by_row[("CHQ", r["cheque_number"].lstrip("0"), cents(r["amount"]))] = x[0]
                    if rows:
                        self.assign(t, rows, "cheque")
                        n["cheque"] += 1
                        continue
            else:
                # payment runs: explicit end-to-end id
                e2e = None
                for tok in re.findall(r"(?:PR|CF)\d{12}-\d{4}", blob):
                    if tok in pr_e2e and tok not in used_batch:
                        e2e = tok
                        break
                if not e2e:
                    c = [x for x in pr_by_amt.get((acc, -amt), []) if x not in used_batch]
                    if c:
                        c.sort(key=lambda x: abs((d(pr_e2e[x][0]["execution_date"]) - bday).days))
                        if abs((d(pr_e2e[c[0]][0]["execution_date"]) - bday).days) <= 5:
                            e2e = c[0]
                if e2e:
                    used_batch.add(e2e)
                    rows = self.run_items(pr_e2e[e2e])
                    if rows:
                        self.assign(t, rows, "pay_transfer")
                        n["pay_transfer"] += 1
                        continue
                # aggregated run
                c = [x for x in agg_by_amt.get((acc, -amt), []) if x not in used_batch]
                if c:
                    used_batch.add(c[0])
                    rows = self.run_items(pr_run[c[0]])
                    if rows:
                        self.assign(t, rows, "pay_aggregated")
                        n["pay_aggregated"] += 1
                        continue
                # confirming: run id in text, debit = documents maturing on this date
                rid = next((tok for tok in re.findall(r"CF\d{12}", blob) if tok in pr_run), None)
                if rid:
                    rows = self.confirming_items(pr_run[rid], -amt, bday, used_batch)
                    if rows:
                        self.assign(t, rows, "confirming")
                        n["confirming"] += 1
                        continue
        log("pass batches:", dict(n))

    def dd_item(self, r, entity):
        its = self.find_doc(r["remittance_info"], side="AR", entity=entity)
        if its:
            return its[0]
        p = self.party_by_mandate.get(r["mandate_id"])
        if p:
            a = cents(r["amount"])
            for it in self.by_party.get(p, ()):
                if it.typ == "ARI" and it.open == a:
                    return it
        return None

    def cheque_item(self, r, entity, settled_ok=False):
        a = cents(r["amount"])
        ref = r["stub_reference"]
        if ref:
            its = self.find_doc(ref, side="AR", entity=entity)
            if not its:
                its = self.by_digits.get((entity, digits(ref)), [])
            its = [i for i in its if i.typ == "ARI"]
            if its:
                its.sort(key=lambda i: (i.open != a, -i.open))
                return (its[0], min(a, its[0].open) if its[0].open < a else a, "" if its[0].open <= a else "PARTIAL")
        # drawer name -> party -> open invoice with same amount
        toks = set(name_tokens(r["drawer"]))
        idx = self.name_idx.get(entity, {})
        sc = Counter()
        for tk in sorted(toks):
            for p in sorted(idx.get(tk, ())):
                sc[p] += self.idf.get(tk, 0)
        # rank drawers by the share of their name matched (not raw idf), try every plausible one
        ranked = sorted(((s / self.party_tot.get(p, 1.0), s, p) for p, s in sc.items()), key=lambda x: (-x[0], -x[1], x[2]))
        for frac, _, p in ranked[:60]:
            if frac < 0.4:
                break
            for it in self.by_party.get(p, ()):
                if it.typ == "ARI" and (it.open == a or (settled_ok and it.open == 0 and it.total == a)):
                    return (it, a, "")
        # last resort: the only open cheque-payable invoice of this entity with exactly this amount
        if not hasattr(self, "_cheque_amt"):
            self._cheque_amt = defaultdict(list)
            for it in self.items.values():
                if it.typ == "ARI" and it.pm == "cheque":
                    self._cheque_amt[(it.entity, it.total)].append(it)
        c = [i for i in self._cheque_amt.get((entity, a), []) if i.open == a]
        if len(c) == 1:
            return (c[0], a, "")
        return None

    def prefer_factor(self, its, payee_text):
        """invoices assigned to a factor (PDF clause) are paid to the factor: keep those whose factor is the payee"""
        pt = set(name_tokens(payee_text))
        if not pt:
            return None
        hit = [i for i in its if i.factor and set(name_tokens(i.factor)) <= pt]
        return hit or None

    def run_items(self, rows):
        out = []
        for r in rows:
            side_typ = "APC" if r["document_type"] == "CREDIT_NOTE" else "API"
            sup = self.party_by_iban.get(r["beneficiary_iban"], set())
            its = [i for i in self.by_doc.get(nalnum(r["document_number"]), []) if i.typ == side_typ]
            da = abs(cents(r["document_amount"]))
            if len(its) > 1:
                its = [i for i in its if i.total == da or i.open == da] or its
            if len(its) > 1:
                its = self.prefer_factor(its, r["beneficiary"]) or its
            if len(its) > 1 and sup:
                its = [i for i in its if i.party in sup] or its
            if len(its) > 1:
                bt = set(name_tokens(r["beneficiary"]))
                its.sort(key=lambda i: -len(bt & set(name_tokens(i.name))))
            if not its and side_typ == "API":  # rappel credit note paid out by transfer: ARC item
                its = [i for i in self.by_doc.get(nalnum(r["document_number"]), [])]
            if its:
                it = its[0]
                amt = abs(cents(r["document_amount"]))
                exc = "PARTIAL" if it.typ == "API" and amt < it.open and amt < it.total else ""
                out.append((it, amt, exc))
        return out

    def confirming_items(self, rows, target, bday, used):
        groups = defaultdict(list)
        for r in rows:
            if r["end_to_end_id"] not in used:
                groups[r["end_to_end_id"]].append(r)
        # documents whose due date is the debit date
        info = []
        for e2e, rs in groups.items():
            its = self.run_items(rs)
            dues = [it.due for it, _, _ in its if it.typ == "API" and it.due]
            info.append((e2e, rs, its, max(dues) if dues else None, cents(rs[0]["transfer_amount"])))
        def roll(dt):  # maturity on a weekend -> next business day
            while dt.weekday() >= 5:
                dt += timedelta(days=1)
            return dt
        sel = [x for x in info if x[3] and roll(x[3]) == bday]
        exc_ = sum(x[4] for x in sel) - target
        if exc_ > 0:  # one or two groups of that maturity were paid separately
            done = False
            for k in (1, 2):
                for comb in combinations(sel, k):
                    if sum(x[4] for x in comb) == exc_:
                        drop = {x[0] for x in comb}
                        sel = [x for x in sel if x[0] not in drop]
                        done = True
                        break
                if done or len(sel) > 80:
                    break
        if sum(x[4] for x in sel) != target:
            # bank holiday: maturities since the previous debit date
            sel = [x for x in info if x[3] and bday - timedelta(days=4) < roll(x[3]) <= bday]
        if sum(x[4] for x in sel) != target:
            sel = [x for x in info if x[3] and abs((x[3] - bday).days) <= 3]
            if sum(x[4] for x in sel) != target:
                # subset of groups by amount (small)
                sel = None
                if len(info) <= 25:
                    for k in range(1, 4):
                        for comb in combinations(info, k):
                            if sum(x[4] for x in comb) == target:
                                sel = list(comb)
                                break
                        if sel:
                            break
                if not sel:
                    return None
        out = []
        for x in sel:
            used.add(x[0])
            out += x[2]
        return out

    # ============================================================== pass 2: DD returns / chargebacks
    def pass_returns(self, txns):
        """R-transactions: direct-debit returns, returned cheques, card chargebacks, our transfers rejected."""
        mandate_rows = defaultdict(list)
        for bid, rows in self.dd_rows.items():
            for r in rows:
                mandate_rows[r["mandate_id"]].append(r)
        cheque_rows = defaultdict(list)
        for did, rows in self.cq_rows.items():
            for r in rows:
                cheque_rows[r["cheque_number"].lstrip("0")].append(r)
        card_rows = defaultdict(list)
        for sid, rows in self.cs_rows.items():
            for r in rows:
                card_rows[r["card"][-4:]].append(r)
        n = Counter()
        for t in txns:
            if t["bank_txn_id"] in self.out:
                continue
            amt = cents(t["amount"])
            blob = " ".join([t["text"], t["remittance_info"], t["ref2"], t["end_to_end_id"]])
            ublob = strip_acc(blob).upper()
            if amt < 0:
                # --- SEPA/BACS/ACH direct-debit return: mandate id
                m = re.search(r"VTM-[A-Z]{2}-\d{7}", blob)
                if m:
                    bday_s = t["booking_date"]
                    rows = [r for r in mandate_rows.get(m.group(0), []) if cents(r["amount"]) == -amt
                            and r["collection_date"] <= bday_s]
                    batch_ids = set(re.findall(r"\d{11}", blob))
                    rows.sort(key=lambda r: (r["batch_id"] in batch_ids, r["collection_date"]), reverse=True)
                    it = None
                    if rows:
                        it = self.settled_by_row.get(("DD", rows[0]["end_to_end_id"])) or                             self.dd_item(rows[0], t["entity"])
                    if not it:
                        p = self.party_by_mandate.get(m.group(0))
                        c = [i for i in self.by_party.get(p, ()) if i.typ == "ARI" and i.total == -amt]
                        if c:
                            c.sort(key=lambda i: i.issue, reverse=True)
                            it = c[0]
                    if it:
                        self.assign(t, [(it, -amt, "RETURNED")], "dd_return")
                        n["dd_return"] += 1
                        continue
                # --- returned cheque: cheque number
                if re.search(r"RETURNED ITEM|CHQ IMPAYE|CHEQUE IMPAYE|CHEQUE DEVUELTO|IMPAYE CHQ|CHEQUE RETOURNE|"
                             r"RCHQ|CHEQUE IMPAYÉ", ublob + " " + t["bank_code"]):
                    it = None
                    for num in re.findall(r"\b\d{6,8}\b", blob):
                        rows = [r for r in cheque_rows.get(num.lstrip("0"), []) if cents(r["amount"]) == -amt]
                        if rows:
                            it = self.settled_by_row.get(("CHQ", num.lstrip("0"), -amt))
                            if not it:
                                x = self.cheque_item(rows[0], t["entity"], settled_ok=True)
                                it = x[0] if x else None
                            if it:
                                break
                    if it:
                        self.assign(t, [(it, -amt, "RETURNED")], "cheque_return")
                        n["cheque_return"] += 1
                        continue
                # --- card chargeback: order number, else card mask + amount
                if re.search(r"CONTRACARGO|CHARGEBACK|RUECKBELASTUNG|IMPAYE CARTE|STORNO KARTE|CHARGE BACK", ublob)                         or "CHRG" in t["bank_code"]:
                    it = None
                    for tok in re.findall(r"[A-Z]{2,4}\d{2}-?\d{5,}", ublob):
                        c = [i for i in self.find_doc(tok, side="AR", entity=t["entity"]) if i.typ == "ARI"]
                        if c:
                            it = c[0]
                            break
                    if not it:
                        mm = re.search(r"\*{2,}(\d{4})", blob)
                        if mm:
                            rows = [r for r in card_rows.get(mm.group(1), []) if cents(r["gross"]) == -amt
                                    and r["account_id"] == t["account_id"]]
                            rows.sort(key=lambda r: r["settlement_date"], reverse=True)
                            for r in rows:
                                c = self.find_doc(r["order_number"], side="AR", entity=t["entity"])
                                if c:
                                    it = c[0]
                                    break
                    if it:
                        self.assign(t, [(it, -amt, "RETURNED")], "chargeback")
                        n["chargeback"] += 1
                        continue
            else:
                # --- our transfer to a supplier rejected by the beneficiary bank: payment-run end-to-end id
                if re.search(r"RETURN|DEVOLUCION TRANSF|RETOUR|RUECKUEBERWEISUNG|RUCKUBERWEISUNG|REJECT|RECHAZ|"
                             r"DEVOLUCAO", ublob) or "RRTN" in t["bank_code"]:
                    rows = None
                    for tok in re.findall(r"(?:PR|CF)\d{12}-\d{4}", blob):
                        if tok in self.pr_e2e:
                            rows = self.pr_e2e[tok]
                            break
                    if rows:
                        its = self.run_items(rows)
                        if its:
                            self.assign(t, [(it, a, "RETURNED") for it, a, _ in its], "transfer_return")
                            n["transfer_return"] += 1
                            continue
        log("pass returns:", dict(n))

    # ============================================================== pass 2b: explicit wording -> category
    def pass_keywords(self, txns):
        n = Counter()
        for t in txns:
            if t["bank_txn_id"] in self.out:
                continue
            amt = cents(t["amount"])
            u = strip_acc(" ".join([t["text"], t["remittance_info"], t["ref2"]])).upper()
            cat = None
            if amt > 0 and re.search(r"PROFORMA|PRO-FORMA|\bPF\d{2}-\d{4,}", u):
                cat = "CUSTOMER_ADVANCE"  # a proforma is not an invoice (POLICIES §4)
            elif amt > 0 and re.search(r"PAGO DOBLE|PAGO DUPLICADO|DUPLICAD|DUPLICATE|DOUBLE PAIEMENT|DOPPELZAHLUNG|"
                                       r"DOBLE\*|PAGAMENTO DUPLICADO", u):
                cat = "SUPPLIER_REFUND"  # supplier gives back a payment we made twice
            elif amt < 0 and re.search(r"REFUND DUPLICATE|DEVOLUCION PAGO DUPLICADO|REMBOURSEMENT DOUBLE|"
                                       r"RUECKERSTATTUNG DOPPEL|ERSTATTUNG DOPPEL|DEVOLUCAO PAGAMENTO DUPLICADO|"
                                       r"REFUND DOUBLE|DEVOLUCION DUPLICADO", u):
                cat = "CUSTOMER_REFUND"
            if cat:
                self.category(t, cat, why="kw_" + cat.lower())
                n[cat] += 1
        log("pass keywords:", dict(n))

    # ============================================================== pass 3: remittance advices
    def load_remittances(self):
        cache_p = os.path.join(self.root, "solution", "cache", f"ra_parsed_v{RA_PARSER_VERSION}.pkl")
        cache = {}
        if os.path.exists(cache_p):
            with open(cache_p, "rb") as fh:
                cache = pickle.load(fh)
        ras, dirty = [], False
        for f, rel in self.aux_files_any("remittance_advices"):
            if rel not in cache:
                try:
                    cache[rel] = parse_remittance(f)
                except Exception:  # noqa
                    cache[rel] = None
                dirty = True
            ra = cache[rel]
            if ra and ra["lines"]:
                ra = dict(ra)
                ra["entity"] = os.path.basename(os.path.dirname(f))
                ras.append(ra)
        if dirty:
            os.makedirs(os.path.dirname(cache_p), exist_ok=True)
            with open(cache_p, "wb") as fh:
                pickle.dump(cache, fh)
        return ras

    def aux_files_any(self, sub):
        lo, hi = self.month_window(before=1, after=1)
        out, seen = [], set()
        for f in self.data_folders():
            idx = os.path.join(self.root, f, sub, "index.csv")
            if not os.path.exists(idx):
                continue
            for r in rcsv(idx):
                ym = r["received_date"][:7].replace("-", "")
                if lo <= ym <= hi and r["file"] not in seen:
                    seen.add(r["file"])
                    out.append((os.path.join(self.root, f, sub, r["file"]), r["file"]))
        return out

    def pass_remittance(self, txns):
        ras = self.load_remittances()
        log(f"remittance advices parsed: {len(ras)}")
        by_amt = defaultdict(list)
        refidx = {}
        for i, ra in enumerate(ras):
            if ra.get("total") is None:
                ra["total"] = sum(x["paid"] for x in ra["lines"])
            by_amt[(ra["entity"], ra["total"])].append(i)
            if ra.get("ref") and len(nalnum(ra["ref"])) >= 6:
                refidx[nalnum(ra["ref"])] = i
        used = set()
        n = 0
        for t in txns:
            if t["bank_txn_id"] in self.out:
                continue
            amt = cents(t["amount"])
            if amt <= 0:
                continue
            blob = nalnum(" ".join([t["text"], t["ref1"], t["ref2"], t["remittance_info"], t["end_to_end_id"]]))
            # amount based
            c = [i for i in by_amt.get((t["entity"], amt), []) if i not in used]
            bday = d(t["booking_date"])
            best = None
            for i in c:
                ra = ras[i]
                score = 0
                if ra.get("ref") and nalnum(ra["ref"]) and nalnum(ra["ref"])[:10] in blob:
                    score += 10
                pt = set(name_tokens(ra.get("payer", "")))
                if pt & set(name_tokens(t["text"] + " " + t["counterparty_name"])):
                    score += 3
                dd_ = abs((d(ra["date"]) - bday).days) if ra.get("date") else 15
                if dd_ > 25:
                    continue
                score -= dd_ / 10
                if best is None or score > best[0]:
                    best = (score, i)
            if best is None:
                # reference match even if amount differs (cash = total minus bank charges etc.)
                for tok in re.findall(r"[A-Z0-9/\-]{6,}", " ".join([t["text"], t["ref2"], t["remittance_info"]])):
                    i = refidx.get(nalnum(tok))
                    if i is not None and i not in used and ras[i]["entity"] == t["entity"]:
                        best = (0, i)
                        break
            if best is None:
                continue
            ra = ras[best[1]]
            rows = self.ra_rows(ra, t)
            if rows:
                used.add(best[1])
                self.assign(t, rows, "remittance")
                n += 1
        log("pass remittance:", n)

    def ra_rows(self, ra, t):
        ent = t["entity"]
        rows = []
        for ln in ra["lines"]:
            doc = ln["doc"]
            its = self.find_doc(doc, side="AR", entity=ent)
            if not its:
                its = self.by_digits.get((ent, digits(doc)), []) if len(digits(doc)) >= 5 else []
            if not its and ln.get("order"):
                its = self.by_doc.get("PO:" + nalnum(ln["order"]), [])
            its = [i for i in its if i.entity == ent] or its
            if not its:
                continue
            it = its[0]
            paid, gross, ded = ln["paid"], ln.get("gross"), ln.get("ded") or 0
            if it.typ == "ARC" or paid < 0:
                rows.append((it, abs(paid), ""))
                continue
            reason = (ln.get("reason") or "").upper()
            if gross is not None and paid != gross and ded:
                if re.search(r"DESC|DISC|SKONTO|ESCOMPTE|PRONTO|EARLY", reason) or not reason:
                    da = self.discount_amount(it, d(t["booking_date"]))
                    if da and da[1]:
                        rows.append((it, it.open, "SHORT_DISCOUNT"))
                    elif da:
                        rows.append((it, paid, "PARTIAL"))
                    else:
                        rows.append((it, paid, "SHORT_DISPUTE" if reason else "PARTIAL"))
                else:
                    rows.append((it, paid, "SHORT_DISPUTE"))
            elif paid < it.open:
                rows.append((it, paid, "PARTIAL"))
            else:
                rows.append((it, it.open if it.open else paid, ""))
        return rows

    # ============================================================== pass 4: references in text
    def ref_candidates(self, t, side):
        ent = t["entity"]
        txt = " ".join([t["text"], t["ref1"], t["ref2"], t["remittance_info"]]).upper()
        found = []
        seen = set()
        toks = re.findall(r"[A-Z0-9][A-Z0-9/\-\.]{3,}[A-Z0-9]", txt)
        for tok in toks:
            c = [i for i in self.by_doc.get(nalnum(tok), []) if i.side == side]
            if not c and side == "AR":
                dg = digits(tok)
                if len(dg) >= 6 and len(dg) >= len(nalnum(tok)) - 4:
                    c = self.by_digits.get((ent, dg), []) or self.by_digits.get((ent, dg.lstrip("0")), [])
            if side == "AR":
                c = [i for i in c if i.entity == ent]
            c = [i.replaced_by if i.replaced_by is not None else i for i in c]
            for i in c:
                if i.id not in seen:
                    seen.add(i.id)
                    found.append(i)
        return found

    def pass_refs(self, txns):
        n = Counter()
        for t in txns:
            if t["bank_txn_id"] in self.out:
                continue
            amt = cents(t["amount"])
            side = "AR" if amt > 0 else "AP"
            # supplier refund of its credit note / our rappel payout
            refs = self.ref_candidates(t, side)
            if amt > 0:
                refs += [i for i in self.ref_candidates(t, "AP") if i.typ == "APC"]
                refs += self.apc_by_related(t)
            else:
                refs += [i for i in self.ref_candidates(t, "AR") if i.typ == "ARC"]
            if not refs:
                continue
            if amt < 0 and len({i.party for i in refs}) > 1:
                refs = self.prefer_factor(refs, " ".join([t["text"], t["counterparty_name"]])) or refs
            r = self.match_amount(t, refs, amt)
            if not r and not any(i.open > 0 for i in refs if i.typ in ("ARI", "API")):
                r = self.dup_check(t, refs, amt)
            if not r:
                r = self.fill_from_party(t, refs, amt)
            if not r:
                r = self.match_amount(t, refs, amt, strict_refs=True)
            if r:
                self.assign(t, r[0], "ref_" + r[1])
                n[r[1]] += 1
        log("pass refs:", dict(n))

    def apc_by_related(self, t):
        """supplier refunds quote the original invoice ("REEMBOLSO FRA FAC2500090"): its credit note is AB-FAC2500090"""
        txt = " ".join([t["text"], t["ref2"], t["remittance_info"]]).upper()
        out = []
        for tok in re.findall(r"[A-Z0-9][A-Z0-9/\-\.]{3,}[A-Z0-9]", txt):
            out += [i for i in self.by_doc.get("REL:" + nalnum(tok), []) if i.open > 0]
        return out

    def dup_check(self, t, cands, amt, max_age_days=400):
        """the item was already settled and is paid again (POLICIES §3 DUPLICATE_PAYMENT)"""
        bday = d(t["booking_date"])
        best = None
        for c in cands:
            if c.typ not in ("ARI", "API") or c.open != 0 or c.cancelled:
                continue
            if c.issue and (bday - c.issue).days > max_age_days:
                continue
            T = self.line_in_item_cur(t, c, amt)
            if T is None:
                continue
            same_cur = c.cur == t["currency"]
            if (same_cur and T == c.total) or (not same_cur and abs(T - c.total) <= 0.03 * c.total):
                if best is None or (c.issue or date.min) > (best.issue or date.min):
                    best = c
        if best:
            return [(best, self.line_in_item_cur(t, best, amt), "DUPLICATE_PAYMENT")], "duplicate"
        return None

    def settled_items_of(self, parties, side):
        out = []
        for p in parties:
            out += [i for i in self.by_party.get(p, ()) if i.side == side and i.open == 0]
        return out

    def fill_from_party(self, t, refs, amt):
        """referenced items are part of the payment (text truncated): complete with other open items of the payer"""
        A = abs(amt)
        refs = [r for r in refs if r.open > 0 and r.cur == t["currency"]]
        if not refs:
            return None
        base = sum(self.signed(r) for r in refs)
        R = A - base
        if R <= 0:
            return None
        parties = sorted({r.party for r in refs})
        ids = {r.id for r in refs}
        others = [i for i in self.open_items_of(parties, refs[0].side) if i.id not in ids and i.cur == t["currency"]]
        vals = [(c, self.signed(c)) for c in others]
        rows0 = [(r, r.open, "") for r in refs]
        if sum(v for _, v in vals) == R:
            return rows0 + [(c, c.open, "") for c, _ in vals], "fill_all"
        s = 0
        for k, (c, v) in enumerate(vals):
            s += v
            if s == R:
                return rows0 + [(c2, c2.open, "") for c2, _ in vals[:k + 1]], "fill_prefix"
        n = len(vals)
        maxk = 3 if n <= 30 else (2 if n <= 80 else 1)
        for k in range(1, maxk + 1):
            for comb in combinations(vals, k):
                if sum(v for _, v in comb) == R:
                    return rows0 + [(c, c.open, "") for c, _ in comb], f"fill_comb{k}"
        return None

    def signed(self, it):
        return it.open if it.typ in ("ARI", "API") else -it.open

    def line_in_item_cur(self, t, it, amt):
        """bank amount converted to item currency (abs cents) or None"""
        if it.cur == t["currency"]:
            return abs(amt)
        if t["orig_currency"] == it.cur and t["orig_amount"]:
            return abs(cents(t["orig_amount"]))
        v = self.convert(abs(amt), t["currency"], it.cur, d(t["booking_date"]))
        return int(round(v)) if v is not None else None

    def match_amount(self, t, cands, amt, strict_refs=False):
        """Try to explain |amt| with a subset of candidate items. Returns (rows, how) or None."""
        A = abs(amt)
        bday = d(t["booking_date"])
        cands = [c for c in cands if c.open > 0 and not c.cancelled]
        if amt > 0:  # a rebate "paid to your bank account" is settled by our transfer, not netted in a receipt
            cands = [c for c in cands if not (c.typ == "ARC" and c.cn_mode == "paid_out")]
        if not cands:
            return None
        cur_items = [c for c in cands if c.cur == t["currency"]]
        fx_items = [c for c in cands if c.cur != t["currency"]]

        def tgt(c):
            return A if c.cur == t["currency"] else self.line_in_item_cur(t, c, amt)

        def eq(c, val, target):
            if target is None:
                return False
            if c.cur == t["currency"] and not (t["orig_currency"] and t["orig_currency"] != t["currency"]):
                return val == target
            return abs(val - target) <= max(2, 0.03 * target)  # FX tolerance

        # 1. single exact (several same-amount invoices: the service month named in the text, else oldest)
        exact = [c for c in cands if c.typ in ("ARI", "API") and eq(c, c.open, tgt(c))]
        kn0 = instalment_k_of_n(" ".join([t["text"], t["remittance_info"], t["ref2"]]))
        if exact and kn0 and kn0[0] < kn0[1]:
            inst = [c for c in exact if c.typ == "ARI" and (c.plan or abs(tgt(c) - int(round(c.total / kn0[1]))) <= kn0[1] + 1)]
            if inst:
                return [(inst[0], tgt(inst[0]), "PARTIAL")], "instalment_text"
        if exact:
            if len(exact) > 1:
                sm = service_month(" ".join([t["text"], t["remittance_info"], t["ref2"]]), bday)
                if sm:
                    # monthly fees are invoiced at the start of the following month
                    y, m = sm[0], sm[1] + 1
                    if m == 13:
                        y, m = y + 1, 1
                    hit = [c for c in exact if c.smonth == sm] or                         [c for c in exact if c.smonth is None and c.issue and (c.issue.year, c.issue.month) == (y, m)]
                    if hit:
                        return [(hit[0], hit[0].open, "")], "exact_month"
            return [(exact[0], exact[0].open, "")], "exact"
        # 1b. partial payments announced by the invoice PDF: instalment, net due after an advance, retention.
        # The bank text often says which instalment it is ("PLAZO 2/3", "INSTALMENT 3/3"): the last one has no exception.
        kn = instalment_k_of_n(" ".join([t["text"], t["remittance_info"], t["ref2"]]))
        for c in cands:
            if c.typ != "ARI":
                continue
            T = tgt(c)
            if T is None or T > c.open:
                continue
            plan_hit = c.plan and any(abs(T - a) <= 1 for a in c.plan)
            split_hit = kn and abs(T - int(round(c.total / kn[1]))) <= kn[1] + 1  # equal split, no PDF
            if plan_hit or split_hit:
                if kn and kn[0] == kn[1]:
                    return [(c, T, "")], "instalment_last"
                if T < c.open:
                    return [(c, T, "PARTIAL")], "instalment"
            if T == c.open:
                continue
            if c.plan and any(abs(T - a) <= 1 for a in c.plan):
                return [(c, T, "PARTIAL")], "instalment"
            if c.adv_net and c.open == c.total and abs(T - c.adv_net) <= 1:
                return [(c, T, "PARTIAL")], "advance"
            if c.payable and c.open == c.total and abs(T - c.payable) <= 1:
                return [(c, T, "PARTIAL")], "retention_pdf"
        # credit note refunded / paid out
        for c in cands:
            if c.typ in ("ARC", "APC") and eq(c, c.open, tgt(c)):
                return [(c, c.open, "")], "cn_exact"
        # 2. discount within terms
        for c in cands:
            da = self.discount_amount(c, bday)
            if da and eq(c, da[0], tgt(c)):
                if da[1]:
                    return [(c, c.open, "SHORT_DISCOUNT")], "discount"
                return [(c, tgt(c), "PARTIAL")], "discount_late"
        # 3. withholding (MX/BR/MA customers only) / guarantee retention / bank charges on a single item
        non_sepa = self._non_sepa(t)
        for c in cands:
            T = tgt(c)
            if c.typ != "ARI" or T is None or T >= c.open or c.open != c.total:
                continue
            if c.entity in WITHHOLDING_ENTITIES:
                for rate in (0.0125, 0.015, 0.10):
                    if abs(c.total - int(round(c.total * rate)) - T) <= 1:
                        return [(c, c.open, "SHORT_WITHHOLDING")], "withholding"
            for rate in (0.05, 0.10):
                if abs(c.total - int(round(c.total * rate)) - T) <= 1:
                    return [(c, T, "PARTIAL")], "retention"
        if non_sepa:
            for c in cands:
                T = tgt(c)
                if c.typ != "ARI" or T is None or T >= c.open or c.cur != t["currency"]:
                    continue
                short = c.open - T
                eur = self.convert(short, c.cur, "EUR", bday) or short
                if eur <= 6000 and short <= 0.02 * c.open:
                    return [(c, c.open, "SHORT_BANK_CHARGES")], "bank_charges"
        # 4. subsets per item currency (credit notes netted)
        by_cur = defaultdict(list)
        for c in cands:
            by_cur[c.cur].append(c)
        for cur, lst in by_cur.items():
            if len(lst) < 2:
                continue
            if cur == t["currency"]:
                T, tol, exact = A, 0, True
            elif t["orig_currency"] == cur and t["orig_amount"]:
                T, tol, exact = abs(cents(t["orig_amount"])), 0, True
            else:
                v = self.convert(A, t["currency"], cur, bday)
                if v is None:
                    continue
                T, tol, exact = int(round(v)), max(2, int(0.025 * v)), False
            sel = self.subset_search(lst, T, tol, exact)
            if sel:
                return [(c, c.open, "") for c in sel[0]], sel[1]
        vals = [(c, self.signed(c)) for c in cur_items[:40]]
        if len(vals) >= 2:
            # discount on all invoices of the set
            tot = 0
            ok = True
            for c, v in vals:
                if c.typ == "ARI":
                    da = self.discount_amount(c, bday)
                    if da and da[1]:
                        tot += da[0]
                        continue
                tot += v
            if tot == A and ok:
                rows = []
                for c, v in vals:
                    da = self.discount_amount(c, bday) if c.typ == "ARI" else None
                    rows.append((c, c.open, "SHORT_DISCOUNT" if da and da[1] else ""))
                return rows, "all_discount"
        # 5. partial: references named, cash below open
        if strict_refs:
            inv = [c for c in cands if c.typ in ("ARI", "API")]
            if len(inv) == 1:
                c = inv[0]
                T = tgt(c)
                if T is not None and T < c.open:
                    return [(c, T, "PARTIAL")], "partial"
                # overpayment: a single invoice paid with a small excess (multi-invoice payments are not overpayments)
                if T is not None and c.open < T <= c.open * 1.105 and c.typ == "ARI":
                    return [(c, c.open, "OVERPAYMENT")], "overpay"
            # several referenced: allocate all fully if sum close
        return None

    def subset_search(self, items, T, tol, exact):
        """Find items whose signed open amounts sum to T (±tol). Customers pay oldest-due first with a few gaps."""
        def ok(s):
            return abs(s - T) <= tol

        sv = lambda c: self.signed(c)  # noqa
        if ok(sum(sv(c) for c in items)):
            return items, "all"
        for name, key in (("prefix_due", lambda c: (c.due or date.min, c.issue or date.min, c.id)),
                          ("prefix_issue", lambda c: (c.issue or date.min, c.id))):
            srt = sorted(items, key=key)
            s = 0
            for k, c in enumerate(srt):
                s += sv(c)
                if k >= 1 and ok(s):
                    return srt[:k + 1], name
        if exact:
            # small combinations among the oldest
            pool = sorted(items, key=lambda c: (c.due or date.min, c.id))[:40]
            n = len(pool)
            maxk = 4 if n <= 15 else (3 if n <= 30 else 2)
            vals = [(c, sv(c)) for c in pool]
            for k in range(2, maxk + 1):
                for comb in combinations(vals, k):
                    if sum(v for _, v in comb) == T:
                        return [c for c, _ in comb], f"comb{k}"
            # oldest-due prefix that overshoots, minus 1-2 items (held back / paid elsewhere)
            srt = sorted(items, key=lambda c: (c.due or date.min, c.issue or date.min, c.id))
            s = 0
            for k, c in enumerate(srt):
                s += sv(c)
                if s > T and k >= 2:
                    for extra in range(0, 4):
                        end = k + 1 + extra
                        if end > len(srt):
                            break
                        P = srt[:end]
                        E = sum(sv(x) for x in P) - T
                        pv = [(x, sv(x)) for x in P]
                        for r in (1, 2):
                            if r == 2 and len(pv) > 60:
                                break
                            for comb in combinations(pv, r):
                                if sum(v for _, v in comb) == E:
                                    drop = {x.id for x, _ in comb}
                                    return [x for x in P if x.id not in drop], f"prefix_skip{r}"
                    break
        return None

    def _fx_eq(self, t, vals, amt):
        tot = sum(v for _, v in vals)
        c0 = vals[0][0]
        T = self.line_in_item_cur(t, c0, amt)
        return T is not None and abs(tot - T) <= 0.03 * T

    def _non_sepa(self, t):
        txt = (t["text"] + " " + t["bank_code"] + " " + t["remittance_info"]).upper()
        if re.search(r"SWIFT|EXTERIOR|EN DIV|FOREIGN|INTERNATIONAL|XBCT|GASTOS SHA|\bSHA\b|\bBEN\b|^13$|TT IN|WIRE",
                     txt) or t["bank_code"].startswith("13") or t["orig_currency"]:
            return True
        iban = t["counterparty_account"] or ""
        return bool(iban) and iban[:2] not in SEPA_COUNTRIES

    # ============================================================== pass 5: payer + amount
    def pass_party(self, txns):
        n = Counter()
        for t in txns:
            if t["bank_txn_id"] in self.out:
                continue
            amt = cents(t["amount"])
            side = "AR" if amt > 0 else "AP"
            parties = self.identify_party(t, side)
            if amt > 0:
                sup = [(p, sc) for p, sc in self.identify_party(t, "AP") if sc >= 0.75][:5]
                if sup and (not parties or sup[0][1] > parties[0][1]):
                    # incoming money from a supplier: refund of one of its credit notes (settles the APC item)
                    cands = [i for i in self.open_items_of([p for p, _ in sup], "AP") if i.typ == "APC"]
                    r = self.match_amount(t, cands, amt) if cands else None
                    if r:
                        self.assign(t, r[0], "party_supplier_" + r[1])
                        n["supplier_" + r[1]] += 1
                        continue
            if not parties:
                continue
            self.payer_known.add(t["bank_txn_id"])
            best = parties[0][1]
            top = [p for p, s in parties if s >= best - 0.25][:10]
            # each candidate party alone (amount disambiguates homonyms), then with its group members
            tries = [[p] for p in top]
            for p in top[:3]:
                c = self.customers.get(p)
                if c and c["group_id"]:
                    tries.append([p] + sorted(x for x in self.group_members[c["group_id"]] if x != p))
            for plist in tries:
                cands = self.open_items_of(plist, side)
                if amt > 0:
                    cands += [i for i in self.open_items_of(plist, "AP") if i.typ == "APC"]
                r = self.match_amount(t, cands, amt)
                if r:
                    self.assign(t, r[0], "party_" + r[1])
                    n[r[1]] += 1
                    break
            else:
                r = self.dup_check(t, self.settled_items_of(top[:3], side), amt, max_age_days=200)
                if r:
                    self.assign(t, r[0], "party_" + r[1])
                    n[r[1]] += 1
        log("pass party:", dict(n))

    # ============================================================== pass 6: categories
    def pass_categories(self, txns, model):
        n = Counter()
        for t in txns:
            if t["bank_txn_id"] in self.out:
                continue
            amt = cents(t["amount"])
            cat = model.predict(t)
            if cat is None or cat == "ITEM":
                cat = "UNIDENTIFIED" if amt > 0 else "BANK_FEES"
            exc = "UNIDENTIFIED" if cat == "UNIDENTIFIED" else ""
            if cat == "UNIDENTIFIED" and UNIDENTIFIED_ONLY_UNKNOWN_PAYER and t["bank_txn_id"] in self.payer_known:
                exc = ""  # payer recognised but amount not explained: most likely a payment we failed to match
            self.category(t, cat, exc, why="cat" if not exc and cat == "UNIDENTIFIED" else "cat")
            n[cat] += 1
        log("pass categories:", dict(n.most_common(8)))

    # ============================================================== write
    def write(self, txns, path):
        op = gzip.open if path.endswith(".gz") else open
        with op(path, "wt", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["bank_txn_id", "item_id", "amount", "category", "exception"])
            for t in txns:
                for iid, amt, cat, exc in self.out[t["bank_txn_id"]]:
                    w.writerow([t["bank_txn_id"], iid, f"{amt / 100:.2f}" if iid else "", cat, exc])


# ----------------------------------------------------------------------------- remittance parsing
RA_PARSER_VERSION = 2


def parse_remittance(path):
    ext = path.rsplit(".", 1)[-1].lower()
    if ext == "csv":
        return parse_ra_table(open(path, encoding="utf-8", errors="replace").read())
    if ext == "xlsx":
        return parse_ra_rows(xlsx_rows(path))
    if ext == "pdf":
        txt = subprocess.run(["pdftotext", "-raw", "-enc", "UTF-8", path, "-"], capture_output=True).stdout.decode(
            "utf-8", "replace")
        return parse_ra_text(txt)
    if ext == "txt":
        return parse_ra_text(open(path, encoding="utf-8", errors="replace").read())
    return None


def xlsx_rows(path):
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = re.findall(r"<t[^>]*>(.*?)</t>", z.read("xl/sharedStrings.xml").decode("utf-8"), re.S)
    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
    rows = []
    for rx in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
        cells = []
        for m in re.finditer(r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(.*?)</c>)', rx, re.S):
            col, attrs, inner = m.group(1), m.group(2), m.group(3) or ""
            ci = 0
            for ch in col:
                ci = ci * 26 + ord(ch) - 64
            while len(cells) < ci - 1:
                cells.append("")
            v = ""
            if 't="inlineStr"' in attrs:
                v = "".join(re.findall(r"<t[^>]*>(.*?)</t>", inner, re.S))
            elif 't="s"' in attrs:
                vv = re.search(r"<v>(.*?)</v>", inner)
                v = shared[int(vv.group(1))] if vv else ""
            else:
                vv = re.search(r"<v>(.*?)</v>", inner)
                v = vv.group(1) if vv else ""
            cells.append(v.replace("&amp;", "&"))
        rows.append(cells)
    return rows


def parse_ra_table(text):
    sep = ";" if text.count(";") > text.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=sep))
    return parse_ra_rows(rows)


HDR = {"doc": r"^(document|documento|doc|factura|invoice|rechnung|facture|n.? ?doc|belegnr|beleg|votre facture|"
              r"our invoice|your invoice|ref)", "gross": r"(gross|bruto|brut|importe|betrag|amount|montant)",
       "ded": r"(deduc|discount|descuento|abzug|skonto|escompte|remise)", "reason": r"(reason|motivo|grund|motif)",
       "paid": r"(paid|pagado|pay[eé]|bezahlt|net|neto|zahlbetrag|regl)", "order": r"(order|pedido|commande|bestell)"}


def parse_ra_rows(rows):
    ra = {"lines": [], "ref": None, "date": None, "total": None, "payer": ""}
    hdr = None
    for r in rows:
        cells = [c.strip() for c in r]
        low = [c.lower() for c in cells]
        if not any(cells):
            continue
        if hdr is None:
            # metadata rows: payment_ref, X, payment_date, Y
            for i, c in enumerate(low):
                if ("ref" in c or "advice" in c or "aviso" in c) and i + 1 < len(cells) and cells[i + 1]:
                    ra["ref"] = ra["ref"] or cells[i + 1]
                if re.match(r"\d{4}-\d{2}-\d{2}$", cells[i]):
                    ra["date"] = ra["date"] or cells[i]
            m = {}
            for k, pat in HDR.items():
                for i, c in enumerate(low):
                    if re.search(pat, c) and i not in m.values():
                        if k == "doc" and not re.match(pat, c):
                            continue
                        m.setdefault(k, i)
                        break
            if "doc" in m and ("paid" in m or "gross" in m):
                hdr = m
            elif len(cells) == 1 and not ra["payer"]:
                ra["payer"] = cells[0]
            continue
        doc = cells[hdr["doc"]] if hdr["doc"] < len(cells) else ""
        if not doc or re.match(r"(total|summe|suma)", doc.lower()):
            if doc.lower().startswith("total"):
                v = parse_amount(cells[hdr.get("paid", hdr.get("gross"))] or "0") if len(cells) > 1 else None
                ra["total"] = v
            continue

        def g(k):
            i = hdr.get(k)
            if i is None or i >= len(cells) or not cells[i]:
                return None
            return parse_amount(cells[i]) if k != "reason" and k != "order" else cells[i]

        paid = g("paid")
        gross = g("gross")
        if paid is None:
            paid = gross
        if paid is None:
            continue
        ded = g("ded")
        ra["lines"].append({"doc": doc, "paid": paid, "gross": gross, "ded": ded, "reason": g("reason") or "",
                            "order": g("order") or ""})
    return ra


def parse_ra_text(txt):
    ra = {"lines": [], "ref": None, "date": None, "total": None, "payer": ""}
    lines = [l.strip() for l in txt.splitlines()]
    for l in lines:
        m = re.search(r"(?:payment ref\.?|ref(?:erencia|érence|erenz)?[:.]?|aviso de pago|avis de paiement|"
                      r"zahlungsavis|subject:.*?)\s*([A-Z0-9][A-Z0-9/\-]{5,})", l, re.I)
        if m and not ra["ref"] and re.search(r"\d", m.group(1)):
            ra["ref"] = m.group(1)
        m = re.search(r"(?:amount|importe|montant|betrag|total)\s*(?:de|of|von|du|:)?\s*(\d[\d.,]*[.,]\d{2})", l, re.I)
        if m and ra["total"] is None:
            ra["total"] = parse_amount(m.group(1).strip())
    if not ra["payer"] and lines:
        ra["payer"] = lines[0]
    for l in lines:
        parts = l.split()
        if len(parts) < 2:
            continue
        doc = parts[0]
        if not re.search(r"\d{3,}", doc) or re.match(r"\d{1,2}[/.]\d{1,2}[/.]\d{2,4}$", doc) or AMT_RE.fullmatch(doc):
            continue
        if re.match(r"(date|fecha|datum)", doc, re.I):
            continue
        rest = l[len(doc):]
        rest_nodate = re.sub(r"\d{1,2}[/.]\d{1,2}[/.]\d{2,4}", " ", rest)
        amts = [parse_amount(a) for a in AMT_RE.findall(rest_nodate)]
        amts = [a for a in amts if a is not None]
        if not amts:
            continue
        reason = re.sub(r"[-\d.,/\s]+", " ", rest_nodate).strip()
        gross, paid = amts[0], amts[-1]
        ded = None
        if len(amts) == 2 and amts[0] > 0 > amts[1]:
            # "gross  -deduction  REASON" (no paid column)
            ded, paid = -amts[1], amts[0] + amts[1]
        elif len(amts) >= 3:
            ded = abs(amts[1])
        elif len(amts) == 2 and amts[0] != amts[1]:
            ded = amts[0] - amts[1]
        ra["lines"].append({"doc": doc, "paid": paid, "gross": gross, "ded": ded, "reason": reason, "order": ""})
    return ra


# ----------------------------------------------------------------------------- category model
def text_sig(t):
    s = strip_acc(t["text"]).upper()
    s = re.sub(r"[^A-Z ]", " ", s)
    w = [x for x in s.split() if len(x) >= 3][:3]
    return " ".join(w)


class CatModel:
    def __init__(self):
        self.tabs = [defaultdict(Counter) for _ in range(3)]

    @staticmethod
    def keys(t):
        sg = "+" if cents(t["amount"]) > 0 else "-"
        return [(t["bank"], t["bank_code"], t["bank_subcode"], sg, text_sig(t)),
                (t["bank"], t["bank_code"], t["bank_subcode"], sg),
                (t["bank_code"], sg)]

    def fit(self, root, folder, exclude_months=()):
        log("training category model on", folder, "excluding", list(exclude_months))
        lab = {}
        for r in rcsv(os.path.join(root, folder, "golden_allocations.csv.gz")):
            if r["bank_txn_id"] not in lab:
                lab[r["bank_txn_id"]] = r["category"] or "ITEM"
        for t in rcsv(os.path.join(root, folder, "bank_transactions.csv.gz")):
            y = lab.get(t["bank_txn_id"])
            if y is None or t["booking_date"][:7].replace("-", "") in exclude_months:
                continue
            for i, k in enumerate(self.keys(t)):
                self.tabs[i]["|".join(k)][y] += 1
        log("category model ready")

    def predict(self, t, min_n=3):
        for i, k in enumerate(self.keys(t)):
            c = self.tabs[i].get("|".join(k))
            if c and sum(c.values()) >= min_n:
                # most frequent non-item category (we only predict for lines left unmatched)
                nonitem = [(y, n) for y, n in c.most_common() if y != "ITEM"]
                tot = sum(c.values())
                if nonitem and nonitem[0][1] / tot >= 0.15:
                    return nonitem[0][0]
                if not nonitem:
                    return "ITEM"
        return None

    def save(self, p):
        with open(p, "w") as fh:
            json.dump([{k: dict(v) for k, v in tab.items()} for tab in self.tabs], fh)

    def load(self, p):
        with open(p) as fh:
            raw = json.load(fh)
        self.tabs = [defaultdict(Counter, {k: Counter(v) for k, v in tab.items()}) for tab in raw]


# ----------------------------------------------------------------------------- main
def main():
    global DEV_MONTHS
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--phase", default="sample", choices=["sample", "dev", "train", "live", "final"])
    ap.add_argument("--months", default=",".join(DEV_MONTHS), help="held-out TRAIN months for --phase dev (YYYYMM,...)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--why", default=None, help="optional csv with the matcher used per line (for debugging)")
    a = ap.parse_args()
    root = os.path.abspath(a.data)
    out = a.out or f"sub_{a.phase}.csv.gz"
    DEV_MONTHS = sorted(a.months.split(","))

    model = CatModel()
    excl = DEV_MONTHS if a.phase == "dev" else []
    mp = os.path.join(root, "solution", "cache", "catmodel%s.json" % ("_excl_" + "_".join(excl) if excl else ""))
    if os.path.exists(mp):
        model.load(mp)
    else:
        os.makedirs(os.path.dirname(mp), exist_ok=True)
        model.fit(root, "train", exclude_months=set(excl))
        model.save(mp)

    rec = Rec(root, a.phase)
    folder = "train" if a.phase == "dev" else a.phase
    txns = list(rcsv(os.path.join(root, folder, "bank_transactions.csv.gz")))
    if a.phase == "dev":
        txns = [t for t in txns if t["booking_date"][:7].replace("-", "") in set(DEV_MONTHS)]
    txns.sort(key=lambda t: (t["booking_date"], t["bank_txn_id"]))
    log(f"{len(txns)} bank lines")
    rec.pass_batches(txns)
    rec.pass_returns(txns)
    rec.pass_keywords(txns)
    rec.pass_remittance(txns)
    rec.pass_refs(txns)
    rec.pass_party(txns)
    rec.pass_categories(txns, model)
    rec.write(txns, out)
    if a.why:
        with open(a.why, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["bank_txn_id", "why"])
            for t in txns:
                w.writerow([t["bank_txn_id"], rec.why.get(t["bank_txn_id"], "")])
    log(f"wrote {out}")


if __name__ == "__main__":
    main()
