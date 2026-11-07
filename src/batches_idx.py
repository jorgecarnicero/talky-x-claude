"""Shared item indexes for the batches / ap_match passes (built once, kept in ctx.cache['bidx'])."""
import re
from collections import defaultdict

from data import norm, norm_words

STOP = {"SL", "SA", "SLU", "SCOOP", "GMBH", "CO", "KG", "LTD", "LIMITED", "LLC", "INC", "CORP", "SARL", "SAS", "SRL",
        "SPA", "BV", "NV", "EK", "AG", "PTY", "DE", "CV", "LDA", "LLP", "PLC", "Y", "E", "THE", "AND", "SP", "Z", "OO",
        "EURL", "GROUP", "LTDA", "EFC", "SOCIEDADE", "UNIPESSOAL", "SARLAU", "HERMANOS"}


def name_tokens(s):
    return {w for w in norm_words(s).split() if len(w) > 1 and w not in STOP}


def digits(s):
    return re.sub(r"\D", "", s or "")


class BIdx:
    def __init__(self, ctx):
        it = ctx.items
        self.ctx = ctx
        self.typ = dict(zip(it.item_id, it.item_type.astype(str)))
        self.ent = dict(zip(it.item_id, it.entity.astype(str)))
        self.party = dict(zip(it.item_id, it.party_id))
        self.pname = dict(zip(it.item_id, it.party_name))
        self.total = dict(zip(it.item_id, it.total))
        self.issue = dict(zip(it.item_id, it.issue_date))
        self.due = dict(zip(it.item_id, it.due_date))
        self.cur = dict(zip(it.item_id, it.currency.astype(str)))
        self.doc = dict(zip(it.item_id, it.doc_number))
        self.doc_idx = defaultdict(list)       # norm(doc) -> items
        self.dig_idx = defaultdict(list)       # last 6 digits of doc -> items
        self.party_items = defaultdict(list)
        docs = it.doc_number.fillna("").astype(str)
        try:
            ndocs = (docs.str.normalize("NFKD").str.encode("ascii", "ignore").str.decode("ascii").str.upper()
                     .str.replace(r"[^A-Z0-9]", "", regex=True))
        except Exception:  # noqa: BLE001
            ndocs = docs.map(norm)
        dgs = docs.str.replace(r"\D", "", regex=True).str[-4:]
        for iid, n, dg, p in zip(it.item_id, ndocs, dgs, it.party_id):
            if n:
                self.doc_idx[n].append(iid)
            if len(dg) == 4:
                self.dig_idx[dg].append(iid)
            self.party_items[p].append(iid)
        self._ncache = {}
        # customers: mandate -> customer, names
        self.mandate = {}
        self.cust_name = defaultdict(set)      # (entity, norm name) -> customer ids
        cu = ctx.masters.get("customers")
        if cu is not None and len(cu):
            for r in cu.itertuples():
                m = getattr(r, "sepa_mandate_id", "")
                if m:
                    self.mandate[m.strip().upper()] = r.customer_id
                for nm in (getattr(r, "name", ""), getattr(r, "trade_name", "")):
                    if nm:
                        self.cust_name[(r.entity, norm(nm))].add(r.customer_id)
        for iid, e, p, nm in zip(it.item_id, it.entity.astype(str), it.party_id, it.party_name):
            if self.typ[iid] == "ARI" and nm:
                self.cust_name[(e, norm(nm))].add(p)

    # ------------------------------------------------------------------ helpers
    def open_amt(self, iid):
        return self.ctx.open.get(iid, 0.0)

    def doc_cands(self, ref, types=None, entities=None):
        n = self._ncache.get(ref)
        if n is None:
            n = self._ncache[ref] = norm(ref)
        out = self.doc_idx.get(n, ())
        return [i for i in out if (types is None or self.typ[i] in types) and (entities is None or self.ent[i] in entities)]

    def resolve_ar(self, entity, ref, amount, parties=None, want_open=True, before=None):
        """Find the AR invoice a receipt (dd / cheque / card) refers to."""
        ents = {entity}
        cands = []
        ref = ref or ""
        if ref:
            cands = self.doc_cands(ref, {"ARI"}, ents)
            if not cands:
                for tok in re.findall(r"\d{4,}", ref):
                    for i in self.dig_idx.get(tok[-4:], []):
                        if self.typ[i] == "ARI" and self.ent[i] in ents and digits(self.doc[i]).endswith(tok):
                            cands.append(i)
        if parties:
            pc = [i for i in cands if self.party[i] in parties]
            if pc or cands:
                cands = pc or cands
        if before:
            cands = [i for i in cands if self.issue[i] <= before]
        if cands:
            return self._best(cands, amount, want_open)
        if parties:
            pool = [i for p in parties for i in self.party_items.get(p, []) if self.typ[i] == "ARI" and self.ent[i] in ents
                    and (before is None or self.issue[i] <= before)]
            if want_open:
                m = [i for i in pool if abs(self.open_amt(i) - amount) < 0.011]
                if m:
                    return min(m, key=lambda i: (self.issue[i], i))
            else:
                m = [i for i in pool if abs(self.total[i] - amount) < 0.011 and i not in self.ctx.open]
                if not m:
                    m = [i for i in pool if abs(self.total[i] - amount) < 0.011]
                if m:
                    return max(m, key=lambda i: (self.issue[i], i))
        return None

    def _best(self, cands, amount, want_open):
        cands = list(dict.fromkeys(cands))
        if len(cands) == 1:
            return cands[0]

        def key(i):
            op = self.open_amt(i)
            return (0 if (want_open and op > 0) or (not want_open and op <= 0) else 1,
                    0 if abs(op - amount) < 0.011 or abs(self.total[i] - amount) < 0.011 else 1,
                    self.issue[i])
        return min(cands, key=key)
