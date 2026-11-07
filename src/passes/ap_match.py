"""Pass `ap_match`: AP-side bank lines not covered by payment-run files.

- supplier direct debits (debit; supplier name in text / counterparty; one open API of that supplier with that amount
  or quoted invoice number)
- supplier refunds of credit notes (credit; open APC of that supplier with that amount or quoted credit-note number)
- other outgoing transfers quoting supplier invoice numbers (FRA/FRAS ...) whose open amounts add up to the debit
  (credit notes netted), with FX handling via orig_amount.
Only claims when supplier identity (name tokens) AND amount agree.
"""
import re
from collections import defaultdict

from batches_idx import name_tokens
from data import norm
from passes.batches import FACTORY_SUBS, _s, get_idx

TOL = 0.011
DUP_WORDS = ("DUPLICAD", "PAGO DOBLE", "DUPLICATE", "DOUBLE PAY", "DOPPELT", "DOPPIO", "EN DOUBLE")
DOC_TOK = re.compile(r"[A-Z0-9][A-Z0-9/\-\.]{2,}[0-9]")


class APMatch:
    def __init__(self, ctx):
        self.ctx = ctx
        self.ix = get_idx(ctx)
        self.stats = defaultdict(int)
        self._sup_by_ent = None
        ba = ctx.masters.get("bank_accounts")
        self.acc_ent = dict(zip(ba.account_id, ba.entity)) if ba is not None and len(ba) else {}
        # supplier name tokens per (party_id)
        it = ctx.items
        ap = it[it.item_type.astype(str).isin(["API", "APC"])]
        self.sup_tokens = {}
        self.sup_norm = {}
        for p, nm in zip(ap.party_id, ap.party_name):
            if p not in self.sup_tokens:
                self.sup_tokens[p] = name_tokens(nm)
                self.sup_norm[p] = norm(nm)
        su = ctx.masters.get("suppliers")
        if su is not None and len(su):
            for r in su.itertuples():
                if r.supplier_id in self.sup_tokens:
                    self.sup_tokens[r.supplier_id] |= name_tokens(r.name)
        # open AP items by (entity, rounded amount) — validated against the live ledger at query time
        self.by_amt = defaultdict(list)
        for iid in ap.item_id:
            op = ctx.open.get(iid)
            if op:
                self.by_amt[(self.ix.ent[iid], round(op, 2))].append(iid)

    def _name_score(self, pid, txt_tokens, txt_norm):
        st = self.sup_tokens.get(pid, set())
        if not st:
            return 0.0
        n = self.sup_norm.get(pid, "")
        if len(n) >= 10 and n[:14] in txt_norm:
            return 1.0
        return len(st & txt_tokens) / len(st)

    def _cands_amount(self, ents, amt, types):
        out = []
        for e in ents:
            for iid in self.by_amt.get((e, round(amt, 2)), []):
                if self.ix.typ[iid] in types and abs(self.ctx.open.get(iid, 0) - amt) < TOL:
                    out.append(iid)
        return out

    def _doc_refs(self, txt, ents, types):
        out = []
        for tok in DOC_TOK.findall(txt):
            for iid in self.ix.doc_cands(tok, types, ents):
                if iid not in out:
                    out.append(iid)
        return out

    def try_line(self, t, txt):
        ent = self.acc_ent.get(t.account_id, t.entity)
        ents = FACTORY_SUBS.get(ent, {ent})
        amt = abs(t.amount)
        types = {"APC"} if t.amount > 0 else {"API"}
        txt_tokens = name_tokens(txt)
        txt_norm = norm(txt)
        fx = isinstance(t.orig_currency, str) and t.orig_currency and t.orig_currency != t.currency \
            and t.orig_amount == t.orig_amount
        if fx:
            amt = float(t.orig_amount)
        # 1) quoted document numbers whose open amounts sum to the line
        refs = [i for i in self._doc_refs(txt, ents, {"API", "APC"}) if self.ctx.open.get(i, 0) > 0.005]
        if refs:
            sgn = 1 if t.amount < 0 else -1
            tot = sum((self.ctx.open[i] if self.ix.typ[i] == "API" else -self.ctx.open[i]) * sgn for i in refs)
            parties = {self.ix.party[i] for i in refs}
            if abs(tot - amt) < max(TOL, 0.0005 * amt if not fx else 0.0) and len(parties) == 1:
                rows = []
                for i in refs:
                    exc = "FX_DIFFERENCE" if (self.ix.cur[i] != t.currency and self.ix.typ[i] == "API") else ""
                    rows.append({"item_id": i, "amount": self.ctx.open[i], "exception": exc})
                self.stats["docref"] += 1
                return self.ctx.allocate(t.bank_txn_id, rows, "ap_match:docref")
        # 2) single open item with this amount from a supplier named in the text
        cands = self._cands_amount(ents, amt, types)
        if not cands:
            return self._dup_refund(t, txt, ents, amt, txt_tokens, txt_norm)
        sc = [(self._name_score(self.ix.party[i], txt_tokens, txt_norm), i) for i in cands]
        best = max(s for s, _ in sc)
        if best < 0.6:
            return False
        top = [i for s, i in sc if s == best]
        if len({self.ix.party[i] for i in top}) > 1:
            return False
        iid = min(top, key=lambda i: (self.ix.issue[i], i))   # FIFO within the supplier
        exc = "FX_DIFFERENCE" if (self.ix.cur[iid] != t.currency and self.ix.typ[iid] == "API") else ""
        self.stats["dd" if t.amount < 0 else "refund"] += 1
        return self.ctx.allocate(t.bank_txn_id, [{"item_id": iid, "amount": self.ctx.open[iid], "exception": exc}],
                                 "ap_match:amount")

    def _dup_refund(self, t, txt, ents, amt, txt_tokens, txt_norm):
        """Supplier refunds a payment we made twice -> category SUPPLIER_REFUND (POLICIES §4). Needs a credit, a
        duplicate keyword or a quoted (already paid) supplier invoice of that amount, and the supplier named."""
        if t.amount <= 0:
            return False
        kw = any(w in txt for w in DUP_WORDS)
        docs = [i for i in self._doc_refs(txt, ents, {"API"}) if abs(self.ix.total[i] - amt) < TOL
                and self.ctx.open.get(i, 0) <= 0.005]
        if not kw and not docs:
            return False
        if docs:
            ok = any(self._name_score(self.ix.party[i], txt_tokens, txt_norm) >= 0.6 for i in docs) or kw
        else:
            ok = self._supplier_named(ents, txt_tokens, txt_norm)
        if not ok:
            return False
        self.stats["dup_refund"] += 1
        return self.ctx.allocate(t.bank_txn_id, [{"category": "SUPPLIER_REFUND", "exception": ""}], "ap_match:duprefund")

    def _supplier_named(self, ents, txt_tokens, txt_norm):
        if self._sup_by_ent is None:
            self._sup_by_ent = defaultdict(set)
            for iid in self.ctx.items.item_id[self.ctx.items.item_type.astype(str) == "API"]:
                self._sup_by_ent[self.ix.ent[iid]].add(self.ix.party[iid])
        for e in ents:
            for p in self._sup_by_ent.get(e, ()):
                if self._name_score(p, txt_tokens, txt_norm) >= 0.8:
                    return True
        return False

    def run(self):
        pend = self.ctx.pending()
        for c in ("orig_amount", "orig_currency", "counterparty_name", "remittance_info", "end_to_end_id"):
            if c not in pend.columns:
                pend = pend.assign(**{c: ""})
        for t in pend.itertuples(index=False):
            txt = " | ".join(_s(getattr(t, k, "")) for k in ("text", "ref1", "ref2", "counterparty_name",
                                                           "remittance_info")).upper()
            try:
                self.try_line(t, txt)
            except Exception as e:  # noqa: BLE001
                self.stats["err"] += 1
                if self.stats["err"] <= 3:
                    print(f"    [ap_match] {t.bank_txn_id}: {type(e).__name__} {e}")
        print("    [ap_match] " + ", ".join(f"{k}={v}" for k, v in sorted(self.stats.items())))


def run(ctx):
    APMatch(ctx).run()
