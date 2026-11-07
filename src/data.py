"""Data loading + shared context (ledger of open items, results) for all passes.

Layout expected under DATA root (see README): masters/, train/, live/, final/ (+ final/masters_final/), starter/.
Big tables are cached as parquet under ../cache (outside git).
"""
import os
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
# Team layout (talky-x-claude main): data extracted at the repo root (masters/, train/, live/, final/, starter/).
# Lead's layout: data in ../dataroot. Env vars override both.
_TEAM = os.path.isdir(os.path.join(REPO, "masters"))
DEFAULT_DATA = os.environ.get("BANKREC_DATA", REPO if _TEAM else os.path.join(REPO, "..", "dataroot"))
CACHE = os.environ.get("BANKREC_CACHE", os.path.join(REPO, "solution", "cache", "v35") if _TEAM
                       else os.path.join(REPO, "..", "cache"))

PHASE_CHAIN = {"sample": ["sample"], "train": ["train"], "live": ["train", "live"], "final": ["train", "live", "final"]}
EXCEPTIONS = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
              "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]

ITEM_COLS = ["item_id", "item_type", "entity", "party_id", "party_name", "doc_number", "issue_date", "due_date",
             "currency", "total", "payment_terms", "po", "payment_method", "related_document", "reason"]


# ---------------------------------------------------------------- text helpers
# PERF: pure functions called millions of times on the same strings (profile: 2.2M calls, ~30 s on a 2-month window).
# Results for str inputs are memoised; anything else follows the original code path unchanged.
def _strip_accents_impl(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


_strip_accents_cached = lru_cache(maxsize=1 << 20)(_strip_accents_impl)


def strip_accents(s):
    return _strip_accents_cached(s) if type(s) is str else _strip_accents_impl(s)


@lru_cache(maxsize=1 << 20)
def _norm_str(s):
    return re.sub(r"[^A-Z0-9]", "", _strip_accents_cached(s).upper())


@lru_cache(maxsize=1 << 20)
def _norm_words_str(s):
    return re.sub(r"[^A-Z0-9]+", " ", _strip_accents_cached(s).upper()).strip()


def norm(s):
    """Upper-case alnum only, accents removed. 'Ré-25/001' -> 'RE25001'."""
    if not isinstance(s, str):
        return ""
    return _norm_str(s) if type(s) is str else re.sub(r"[^A-Z0-9]", "", strip_accents(s).upper())


def norm_words(s):
    if not isinstance(s, str):
        return ""
    return _norm_words_str(s) if type(s) is str else re.sub(r"[^A-Z0-9]+", " ", strip_accents(s).upper()).strip()


def full_text(t):
    """All free-text fields of a bank row joined (upper-case)."""
    parts = [t.get(k) for k in ("text", "ref1", "ref2", "counterparty_name", "remittance_info", "end_to_end_id")]
    return " | ".join(p for p in parts if isinstance(p, str)).upper()


# ---------------------------------------------------------------- loading
def _read(path, **kw):
    return pd.read_csv(path, dtype=str, keep_default_na=False, **kw)


def _cached(name, builder):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, name + ".parquet")
    if os.path.exists(p):
        return pd.read_parquet(p)
    df = builder()
    df.to_parquet(p, index=False)
    return df


def load_items(root, ph):
    """Unified item table for one phase folder."""
    def build():
        out = []
        p = os.path.join(root, ph, "ar_invoices.csv.gz")
        if os.path.exists(p):
            d = _read(p)
            out.append(pd.DataFrame({
                "item_id": d.item_id, "item_type": "ARI", "entity": d.entity, "party_id": d.customer_id,
                "party_name": d.customer_name, "doc_number": d.document_number, "issue_date": d.issue_date,
                "due_date": d.due_date, "currency": d.currency, "total": d.total_amount,
                "payment_terms": d.payment_terms, "po": d.customer_po, "payment_method": d.payment_method,
                "related_document": "", "reason": ""}))
        p = os.path.join(root, ph, "ap_invoices.csv.gz")
        if os.path.exists(p):
            d = _read(p)
            out.append(pd.DataFrame({
                "item_id": d.item_id, "item_type": "API", "entity": d.entity, "party_id": d.supplier_id,
                "party_name": d.supplier_name, "doc_number": d.supplier_invoice_number, "issue_date": d.issue_date,
                "due_date": d.due_date, "currency": d.currency, "total": d.total_amount,
                "payment_terms": d.payment_terms, "po": d.our_po, "payment_method": d.payment_method,
                "related_document": "", "reason": ""}))
        p = os.path.join(root, ph, "credit_notes.csv.gz")
        if os.path.exists(p):
            d = _read(p)
            out.append(pd.DataFrame({
                "item_id": d.item_id, "item_type": d.item_id.str[:3], "entity": d.entity, "party_id": d.party_id,
                "party_name": d.party_name, "doc_number": d.document_number, "issue_date": d.issue_date,
                "due_date": d.issue_date, "currency": d.currency, "total": d.total_amount,
                "payment_terms": "", "po": "", "payment_method": "",
                "related_document": d.related_document, "reason": d.reason}))
        df = pd.concat(out, ignore_index=True)
        df["total"] = df["total"].astype(float)
        return df[ITEM_COLS]
    return _cached(f"items_{ph}", build)


def load_bank(root, ph):
    def build():
        d = _read(os.path.join(root, ph, "bank_transactions.csv.gz"))
        d["amount"] = d["amount"].astype(float)
        d["orig_amount"] = pd.to_numeric(d["orig_amount"].replace("", None), errors="coerce")
        return d
    return _cached(f"bank_{ph}", build)


def load_masters(root, phase):
    """masters/ overlaid (file by file) with final/masters_final/ for FINAL — works whether masters_final is complete
    or only holds the changed files."""
    dirs = [os.path.join(root, "masters")]
    fm = os.path.join(root, "final", "masters_final")
    if phase == "final" and os.path.isdir(fm):
        dirs.append(fm)
    m = {}
    for mdir in dirs:
        for f in os.listdir(mdir):
            if f.endswith(".csv"):
                m[f[:-4]] = _read(os.path.join(mdir, f))
    return m


def _items_from_open(root, phase_dir, known):
    """Rows for items that exist only in open_items_at_start (e.g. balances of an acquired subsidiary)."""
    p = os.path.join(phase_dir, "open_items_at_start.csv.gz")
    if not os.path.exists(p):
        return None
    oi = _read(p)
    oi = oi[~oi.item_id.isin(known)]
    if not len(oi):
        return None
    return pd.DataFrame({
        "item_id": oi.item_id, "item_type": oi.item_type, "entity": oi.entity, "party_id": oi.party_id,
        "party_name": "", "doc_number": oi.document_number, "issue_date": oi.issue_date, "due_date": oi.due_date,
        "currency": oi.currency, "total": oi.total_amount.astype(float), "payment_terms": "", "po": "",
        "payment_method": "", "related_document": "", "reason": ""})[ITEM_COLS]


# ---------------------------------------------------------------- context
class Ctx:
    """Shared state handed to every pass.

    bank      : DataFrame of the bank lines to reconcile (sorted by booking_date, bank_txn_id)
    items     : DataFrame of every known item (index = item_id), see ITEM_COLS
    open      : dict item_id -> current open amount (item currency). Updated by allocate().
    masters   : dict name -> DataFrame (customers, suppliers, entities, bank_accounts, payment_terms, fx_rates, categories)
    side_dirs : list of phase folders holding side files (batches, payment runs, remittances, pdfs), oldest first
    results   : dict bank_txn_id -> list of row dicts
    """

    def __init__(self, root, phase, dfrom=None, dto=None, golden_opening=True):
        self.root, self.phase = root, phase
        chain = PHASE_CHAIN[phase]
        self.side_dirs = [os.path.join(root, p) for p in chain]
        self.phase_dir = self.side_dirs[-1]
        self.masters = load_masters(root, phase)
        items = pd.concat([load_items(root, p) for p in chain], ignore_index=True)
        items = items.drop_duplicates("item_id", keep="last")
        if phase in ("live", "final"):
            extra = _items_from_open(root, self.phase_dir, set(items.item_id))
            if extra is not None:
                print(f"  + {len(extra)} open items known only from open_items_at_start", flush=True)
                items = pd.concat([items, extra], ignore_index=True)
        bank = load_bank(root, phase)
        if dfrom:
            bank = bank[bank.booking_date >= dfrom]
        if dto:
            bank = bank[bank.booking_date <= dto]
            items = items[items.issue_date <= dto]
        self.bank = bank.sort_values(["booking_date", "bank_txn_id"]).reset_index(drop=True)
        self.items = items.set_index("item_id", drop=False)
        self.open = self._opening(dfrom, golden_opening)
        # memory trim: keep open items + anything issued in the 2 years before the first bank line (duplicates,
        # replaced invoices, returns need recently-closed items too)
        start = self.bank.booking_date.min() if len(self.bank) else (dfrom or "2000-01-01")
        horizon = str(pd.Timestamp(start) - pd.Timedelta(days=730))[:10]
        keep = self.items.item_id.isin(set(self.open)) | (self.items.issue_date >= horizon)
        self.items = self.items[keep].copy()
        for c in ("item_type", "entity", "currency", "payment_terms", "payment_method", "reason"):
            self.items[c] = self.items[c].astype("category")
        self.results = {}
        self.source = {}   # bank_txn_id -> pass name (diagnostics)
        self.cache = {}    # free scratch space shared between passes

    def _opening(self, dfrom, golden_opening):
        it = self.items
        if self.phase in ("live", "final"):
            oi = _read(os.path.join(self.phase_dir, "open_items_at_start.csv.gz"))
            op = dict(zip(oi.item_id, oi.open_amount.astype(float)))
            start = self.bank.booking_date.min()
            new = it[it.issue_date >= {"live": "2025-07-01", "final": "2026-01-01"}[self.phase]]
            op.update(zip(new.item_id, new.total))
            return op
        op = dict(zip(it.item_id, it.total))
        if dfrom and golden_opening:
            # Simulate LIVE's open_items_at_start for a TRAIN window: subtract what the ledger cleared before dfrom.
            # (Uses labels ONLY for balances before the window, which is exactly what LIVE/FINAL hand us.)
            g = _cached("golden_train", lambda: _read(os.path.join(self.root, "train", "golden_allocations.csv.gz")))
            dates = dict(zip(*[load_bank(self.root, "train")[c] for c in ("bank_txn_id", "booking_date")]))
            g = g[g.item_id != ""]
            g = g[g.bank_txn_id.map(dates) < dfrom]
            amt = g.amount.astype(float)
            sign = g.exception.map(lambda e: 1.0 if e == "RETURNED" else (0.0 if e == "DUPLICATE_PAYMENT" else -1.0))
            delta = (amt * sign).groupby(g.item_id).sum()
            for k, v in delta.items():
                if k in op:
                    op[k] += v
            # train-lite artifact: ~214k items issued in 2021 never appear in the golden and are NOT open in LIVE's
            # real open_items_at_start (checked: simulated 2025-07-01 balances agree 99.7% with the real ones once
            # these are removed). Drop never-settled 2021 items.
            ever = set(self._golden_items())
            old = set(it.item_id[(it.issue_date < "2022-01-01")])
            op = {k: v for k, v in op.items() if v > 0.005 and (k in ever or k not in old)}
        return op

    def _golden_items(self):
        g = _cached("golden_train", lambda: _read(os.path.join(self.root, "train", "golden_allocations.csv.gz")))
        return g.item_id[g.item_id != ""].unique()

    # --- helpers for passes
    def done(self, txn_id):
        return txn_id in self.results

    def pending(self):
        """Bank rows not yet resolved (DataFrame view)."""
        return self.bank[~self.bank.bank_txn_id.isin(self.results.keys())]

    def allocate(self, txn_id, rows, source=""):
        """rows: list of dicts. Item rows: {item_id, amount, exception}. No-item row: {category, exception}.
        Updates the open-item ledger. Returns False if txn already resolved."""
        if txn_id in self.results:
            return False
        clean = []
        for r in rows:
            iid = r.get("item_id", "")
            exc = r.get("exception", "") or ""
            if iid:
                a = round(float(r["amount"]), 2)
                clean.append({"item_id": iid, "amount": a, "category": "", "exception": exc})
                if exc == "RETURNED":
                    self.open[iid] = self.open.get(iid, 0.0) + a
                elif exc in ("DUPLICATE_PAYMENT",):
                    pass
                elif exc in ("PARTIAL", "SHORT_DISPUTE"):
                    self.open[iid] = self.open.get(iid, 0.0) - a
                else:  # cleared in full (incl. SHORT_*/FX/OVERPAYMENT)
                    self.open[iid] = 0.0
                if self.open.get(iid, 0.0) <= 0.005:
                    self.open.pop(iid, None)
            else:
                clean.append({"item_id": "", "amount": "", "category": r["category"], "exception": exc})
        self.results[txn_id] = clean
        self.source[txn_id] = source
        return True


def party_index(ctx):
    """Lazy index: (entity, party_id) -> list of open item ids (all types)."""
    if "party_idx" not in ctx.cache:
        idx = defaultdict(list)
        it = ctx.items
        for iid, ent, pid in zip(it.item_id, it.entity, it.party_id):
            idx[pid].append(iid)
        ctx.cache["party_idx"] = idx
    return ctx.cache["party_idx"]
