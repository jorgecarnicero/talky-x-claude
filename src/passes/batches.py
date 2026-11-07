"""Pass `batches`: bank lines whose content is listed exactly in a side file (POLICIES §3, §5.3).

- direct_debit_batches/ : batch credit clears every receipt; R-transactions (returned receipts) -> RETURNED
- card_settlements/     : settlement credit clears orders at gross; chargebacks -> RETURNED
- cheque_deposits/      : deposit clears cheques; returned cheques -> RETURNED
- payment_runs/         : transfers (per end_to_end_id), aggregated (whole run), confirming (per maturity date);
                          credit notes netted, PARTIAL, FX_DIFFERENCE, DUPLICATE_PAYMENT, returned transfers.
Only claims a line when the side file identifies it and every listed document resolves to an item.
"""
import re
from collections import defaultdict

import pandas as pd

from batches_idx import BIdx, name_tokens
from batches_io import load_side
from data import norm

TOL = 0.011
E2E_RE = re.compile(r"((?:PR|CF)\d{12})(?:-(\d{1,4})(D?))?")
MANDATE_RE = re.compile(r"VTM-[A-Z]{2}-\d+")
CARD_RE = re.compile(r"\*{4}\s?(\d{4})")
CHQ_RE = re.compile(r"(?:CHK|CHQ|CHEQUE|IMPAYE|SCHECK|ASSEGNO)\D{0,12}(\d{6,8})")
ID11_RE = re.compile(r"(?<!\d)(\d{11})(?!\d)")
RET_WORDS = ("RETURN", "DEVOL", "RUECK", "RÜCK", "REJET", "RETOUR", "IMPAY", "UNPAID", "RECHAZ", "STORNO", "RIFIUT")
CB_WORDS = ("CHARGEBACK", "CONTRACARGO", "IMPAYE CB", "RUECKBELASTUNG", "STORNO KARTE", "RIADDEBITO")
FACTORY_SUBS = {"ES00": {"ES00", "ES01", "ES02", "PT01"}}


def _s(x):
    return x if isinstance(x, str) else ""


def get_idx(ctx):
    if "bidx" not in ctx.cache:
        ctx.cache["bidx"] = BIdx(ctx)
    return ctx.cache["bidx"]


def _days(a, b):
    try:
        return (pd.Timestamp(a) - pd.Timestamp(b)).days
    except Exception:  # noqa: BLE001
        return 9999


class Batches:
    def __init__(self, ctx):
        self.ctx = ctx
        self.ix = get_idx(ctx)
        self.stats = defaultdict(int)
        self.receipt_item = {}   # (batch_id, e2e) -> [(item, amount)]
        self.cheque_item = {}    # (deposit_id, cheque_number) -> [(item, amount)]
        self.order_item = {}     # order_number -> item
        self.cache_used_e2e = set()
        self.done_batches = set()
        self.win_start = ctx.bank.booking_date.min() if len(ctx.bank) else ""
        self.paid_lines = defaultdict(list)   # (account, -amount) -> [(date, rows, name tokens)] of our run payments
        self._load()

    # ---------------------------------------------------------------- loading
    def _load(self):
        c = self.ctx
        self.dd = load_side(c, "direct_debit_batches")
        self.cards = load_side(c, "card_settlements")
        self.chq = load_side(c, "cheque_deposits")
        self.runs = load_side(c, "payment_runs", months_before=14)
        self.acc_ent = {}
        ba = c.masters.get("bank_accounts")
        if ba is not None and len(ba):
            self.acc_ent = dict(zip(ba.account_id, ba.entity))
            self.acc_cur = dict(zip(ba.account_id, ba.currency))
        # DD
        self.dd_batch, self.dd_by_amt, self.dd_by_mandate = {}, defaultdict(list), defaultdict(list)
        if len(self.dd) and {"batch_id", "amount"} <= set(self.dd.columns):
            for bid, g in self.dd.groupby("batch_id", sort=False):
                self.dd_batch[bid] = g
                tot = g["batch_total"].iloc[0] if "batch_total" in g else g["amount"].sum()
                if pd.isna(tot):
                    tot = g["amount"].sum()
                self.dd_by_amt[(g["account_id"].iloc[0] if "account_id" in g else "", round(float(tot), 2))].append(bid)
            for r in self.dd.itertuples(index=False):
                m = _s(getattr(r, "mandate_id", "")).strip().upper()
                if m:
                    self.dd_by_mandate[m].append(r)
        # cards
        self.card_settle, self.card_by_amt, self.card_by_mask = {}, defaultdict(list), defaultdict(list)
        if len(self.cards) and {"settlement_id", "net", "gross"} <= set(self.cards.columns):
            for sid, g in self.cards.groupby("settlement_id", sort=False):
                self.card_settle[sid] = g
                self.card_by_amt[(g["account_id"].iloc[0], round(float(g["net"].sum()), 2))].append(sid)
            for r in self.cards.itertuples(index=False):
                m = re.sub(r"\D", "", _s(getattr(r, "card", "")))[-4:]
                if m:
                    self.card_by_mask[m].append(r)
        # cheques
        self.dep, self.dep_by_amt, self.chq_by_num = {}, defaultdict(list), defaultdict(list)
        if len(self.chq) and {"deposit_id", "amount"} <= set(self.chq.columns):
            for did, g in self.chq.groupby("deposit_id", sort=False):
                self.dep[did] = g
                tot = g["deposit_total"].iloc[0] if "deposit_total" in g else g["amount"].sum()
                if pd.isna(tot):
                    tot = g["amount"].sum()
                self.dep_by_amt[(g["account_id"].iloc[0], round(float(tot), 2))].append(did)
            for r in self.chq.itertuples(index=False):
                n = re.sub(r"\D", "", _s(getattr(r, "cheque_number", "")))
                if n:
                    self.chq_by_num[n.lstrip("0")].append(r)
        self.dep_re = None
        if self.dep:
            pre = sorted({re.match(r"[A-Z]*", d).group(0) for d in self.dep if re.match(r"[A-Z]+", d)})
            if pre:
                self.dep_re = re.compile(r"((?:%s)\d{8,})" % "|".join(map(re.escape, pre)))
        self.sup_iban = defaultdict(set)
        su = c.masters.get("suppliers")
        if su is not None and "iban" in su.columns:
            for sid, ib in zip(su.supplier_id, su.iban):
                if ib:
                    self.sup_iban[norm(ib)].add(sid)
        # payment runs
        self.run_rows, self.e2e_rows, self.run_type = {}, defaultdict(list), {}
        self.tr_by_acc_amt = defaultdict(list)
        if len(self.runs) and {"run_id", "document_number"} <= set(self.runs.columns):
            for rid, g in self.runs.groupby("run_id", sort=False):
                self.run_rows[rid] = g
                self.run_type[rid] = _s(g["run_type"].iloc[0]).lower() if "run_type" in g else "transfers"
            if "end_to_end_id" in self.runs.columns:
                for e2e, g in self.runs.groupby("end_to_end_id", sort=False):
                    self.e2e_rows[e2e] = g
                    r0 = g.iloc[0]
                    if self.run_type.get(r0["run_id"]) == "transfers":
                        self.tr_by_acc_amt[(_s(r0.get("debtor_account_id")), round(float(r0["transfer_amount"]), 2))].append(e2e)

    def _same_acc(self, g, acc, col):
        """Ids (run/batch/deposit) are not unique across entities: keep rows of this bank account (or entity)."""
        if g is None or col not in g.columns:
            return g
        s = g[g[col] == acc]
        if len(s):
            return s
        ent = self.acc_ent.get(acc, acc[:4])
        s = g[g[col].map(lambda a: self.acc_ent.get(a, a[:4])) == ent]
        return s if len(s) else None

    # ---------------------------------------------------------------- payment runs
    def _ap_rows(self, rows, bank_cur, ent, bank_date, returned=False):
        """rows: DataFrame of run rows -> list of allocation rows or None if any document fails to resolve."""
        ix = self.ix
        ents = FACTORY_SUBS.get(ent, {ent})
        out, used, miss = [], set(), 0
        for r in rows.itertuples(index=False):
            dt = _s(getattr(r, "document_type", "")).upper()
            amt = abs(float(r.document_amount))
            is_cn = dt.startswith("CREDIT") or float(r.document_amount) < 0
            types = {"APC"} if is_cn else {"API"}
            cands = ix.doc_cands(r.document_number, types, ents)
            if not cands:
                cands = ix.doc_cands(r.document_number, types, None)
            cands = [i for i in cands if i not in used]
            if not cands:
                miss += 1
                self.stats["ap_doc_unresolved"] += 1
                continue
            if len(cands) > 1:
                bt = name_tokens(_s(getattr(r, "beneficiary", "")))
                biban = norm(_s(getattr(r, "beneficiary_iban", "")))
                rcur = _s(getattr(r, "currency", ""))
                sup_ib = self.sup_iban.get(biban, ()) if biban else ()

                def k(i):
                    op = ix.open_amt(i)
                    open_rank = (0 if op <= 0.005 else 1) if returned else (0 if op > 0.005 else 1)
                    return (0 if abs(op - amt) < TOL or abs(ix.total[i] - amt) < TOL else 1,
                            0 if (not rcur or ix.cur.get(i) == rcur) else 1,
                            0 if ix.party[i] in sup_ib else 1,
                            -len(bt & name_tokens(ix.pname[i])), open_rank, ix.issue[i])
                cands.sort(key=k)
            iid = cands[0]
            used.add(iid)
            op = ix.open_amt(iid)
            if returned:
                out.append({"item_id": iid, "amount": amt, "exception": "RETURNED"})
            elif is_cn:
                out.append({"item_id": iid, "amount": amt, "exception": ""})
            elif op <= 0.005:
                out.append({"item_id": iid, "amount": amt, "exception": "DUPLICATE_PAYMENT"})
            elif amt < op - max(0.01, 0.005 * op):
                out.append({"item_id": iid, "amount": amt, "exception": "PARTIAL"})
            else:
                fx = ix.cur.get(iid, bank_cur) != bank_cur
                out.append({"item_id": iid, "amount": op, "exception": "FX_DIFFERENCE" if fx else ""})
        # big aggregated files: tolerate a few unresolved documents; per-transfer lines must resolve fully
        if miss and (len(rows) < 20 or miss > 0.05 * len(rows)):
            return None
        return out

    def try_run(self, t, txt):
        ms = E2E_RE.findall(txt)
        if not ms:
            return False
        amt = abs(t.amount)
        oc = _s(getattr(t, "orig_currency", ""))
        oa = getattr(t, "orig_amount", None)
        if oc and oc != t.currency and isinstance(oa, float) and oa == oa and oa > 0:
            amt = oa
        ent = self.acc_ent.get(t.account_id, t.entity)
        returned = t.amount > 0
        if returned and not any(w in txt for w in RET_WORDS):
            return False
        rows = None
        # 1) full end_to_end id
        for rid, seq, dflag in ms:
            if len(seq) == 4:
                e2e = f"{rid}-{seq}"
                rows = self._same_acc(self.e2e_rows.get(e2e), t.account_id, "debtor_account_id")
                if rows is not None:
                    break
        # 2) run id only
        if rows is None:
            for rid, seq, dflag in ms:
                g = self._same_acc(self.run_rows.get(rid), t.account_id, "debtor_account_id")
                if g is None:
                    continue
                rt = self.run_type.get(rid, "transfers")
                if rt == "aggregated" or (rt != "transfers" and abs(g["document_amount"].sum() - amt) < TOL):
                    rows = g
                    break
                if rt == "confirming":
                    rows = self._confirming_subset(g, amt, t.booking_date, txt)
                    if rows is not None:
                        break
                    continue
                # transfers: pick the transfer with this amount
                tr = g[(g["transfer_amount"] - amt).abs() < TOL]
                if not len(tr) and "currency" in g.columns:
                    # FX transfer without instructed amount on the statement: foreign-currency transfers by name
                    tr = g[g["currency"] != t.currency]
                    if len(tr):
                        tt = name_tokens(txt)
                        sc = tr["beneficiary"].map(lambda b: len(tt & name_tokens(_s(b))))
                        tr = tr[sc == sc.max()] if sc.max() > 0 else tr.iloc[0:0]
                e2es = tr["end_to_end_id"].unique() if len(tr) else []
                if len(e2es) > 1:
                    seqm = re.findall(r"\b0*(\d{1,4})\b", t.text if isinstance(t.text, str) else "")
                    pick = [e for e in e2es if e.rsplit("-", 1)[-1].lstrip("0") in set(s.lstrip("0") for s in seqm)]
                    if len(pick) != 1:
                        tt = name_tokens(txt)
                        sc = {e: len(tt & name_tokens(_s(g[g["end_to_end_id"] == e]["beneficiary"].iloc[0]))) for e in e2es}
                        best = max(sc.values())
                        pick = [e for e in e2es if sc[e] == best] if best > 0 else []
                    e2es = pick
                if len(e2es) == 1:
                    rows = g[g["end_to_end_id"] == e2es[0]]
                    break
        if rows is None:
            return False
        out = self._ap_rows(rows, t.currency, ent, t.booking_date, returned=returned)
        if not out:
            return False
        self.stats["run_return" if returned else "run"] += 1
        if not returned:
            self.paid_lines[(t.account_id, round(t.amount, 2))].append((t.booking_date, out, name_tokens(txt)))
        return self.ctx.allocate(t.bank_txn_id, out, "batches:run")

    def try_transfer_return(self, t, txt):
        """Our transfer rejected by the beneficiary bank, return text without end_to_end id: same account, same
        amount as a supplier payment we cleared in the previous 90 days -> every item RETURNED."""
        if t.amount <= 0 or not any(w in txt for w in RET_WORDS):
            return False
        c = [x for x in self.paid_lines.get((t.account_id, round(-t.amount, 2)), [])
             if 0 <= _days(t.booking_date, x[0]) <= 90]
        if len(c) > 1:
            tt = name_tokens(txt)
            best = max(len(tt & x[2]) for x in c)
            c = [x for x in c if len(tt & x[2]) == best] if best > 0 else []
        if len(c) != 1:
            return False
        rows = [{"item_id": r["item_id"], "amount": r["amount"], "exception": "RETURNED"} for r in c[0][1]]
        self.paid_lines[(t.account_id, round(-t.amount, 2))].remove(c[0])
        self.stats["run_return_noe2e"] += 1
        return self.ctx.allocate(t.bank_txn_id, rows, "batches:runret")

    def _confirming_subset(self, g, amt, bdate, txt):
        """Confirming remittance: one debit per maturity date = a set of whole transfers (end_to_end ids) of the
        run whose amounts add up to the debit ("REMESA CF... N PROVEEDORES" gives N when present)."""
        m = re.search(r"(\d+)\s*PROVEED", txt)
        n_want = int(m.group(1)) if m else None
        used = self.cache_used_e2e
        target = round(amt * 100)
        grp = []
        for e2e, s in g.groupby("end_to_end_id", sort=False):
            if e2e in used:
                continue
            tot = round(float(s["document_amount"].sum()) * 100)
            cl = [self.ix.doc_cands(d, None, None) for d in s["document_number"]]
            if cl and all(c and all(self.ix.open_amt(i) <= 0.005 for i in c) for c in cl):
                continue   # already paid by an earlier debit (possibly before the window)
            dues = [self.ix.due[c[0]] for c in cl if c]
            md = max(dues) if dues else "9999"
            grp.append((e2e, tot, abs(_days(bdate, md)), md <= bdate))
        grp.sort(key=lambda x: x[2])
        grp = [x for x in grp if x[1] <= target]
        def search(pool):
            sol, budget = [None], [50000]

            def dfs(k, left, chosen):
                if budget[0] <= 0 or sol[0] is not None:
                    return
                budget[0] -= 1
                if left == 0 and (n_want is None or len(chosen) == n_want):
                    sol[0] = list(chosen)
                    return
                if n_want is not None and len(chosen) >= n_want:
                    return
                for x in range(k, len(pool)):
                    if pool[x][1] <= left:
                        chosen.append(pool[x][0])
                        dfs(x + 1, left - pool[x][1], chosen)
                        chosen.pop()
                        if sol[0] is not None:
                            return
            dfs(0, target, [])
            return sol[0]
        sol = [search(grp)]
        if not sol[0]:
            return None
        used.update(sol[0])
        return g[g["end_to_end_id"].isin(sol[0])]

    # ---------------------------------------------------------------- direct debits
    def _receipt_items(self, r, ent, want_open=True, before=None):
        key = (_s(r.batch_id), _s(getattr(r, "end_to_end_id", "")), _s(getattr(r, "account_id", "")))
        if key in self.receipt_item:
            return self.receipt_item[key]
        cust = self.ix.mandate.get(_s(getattr(r, "mandate_id", "")).strip().upper())
        parties = {cust} if cust else None
        if not parties:
            nm = norm(_s(getattr(r, "debtor_name", "")))
            parties = self.ix.cust_name.get((ent, nm))
        iid = self.ix.resolve_ar(ent, _s(getattr(r, "remittance_info", "")), float(r.amount), parties,
                                 want_open=want_open, before=before)
        res = [(iid, float(r.amount))] if iid else None
        if res:
            self.receipt_item[key] = res
        return res

    def _receipt_rows(self, g, ent, exc=""):
        out, seen = [], set()
        for r in g.itertuples(index=False):
            res = self._receipt_items(r, ent, want_open=(exc != "RETURNED"))
            if not res:
                return None
            for iid, a in res:
                if iid in seen:
                    return None
                seen.add(iid)
                if exc == "RETURNED":
                    out.append({"item_id": iid, "amount": a, "exception": "RETURNED"})
                else:
                    op = self.ix.open_amt(iid)
                    if op > 0.005 and a < op - max(0.01, 0.005 * op):
                        out.append({"item_id": iid, "amount": a, "exception": "PARTIAL"})
                    else:
                        out.append({"item_id": iid, "amount": op if op > 0.005 else a,
                                    "exception": "" if op > 0.005 else "DUPLICATE_PAYMENT"})
        return out

    def try_dd_batch(self, t, txt):
        if t.amount <= 0 or not self.dd_batch:
            return False
        bid = None
        g = None
        for tok in ID11_RE.findall(txt):
            g = self._same_acc(self.dd_batch.get(tok), t.account_id, "account_id")
            if g is not None:
                bid = tok
                break
        if bid is None:
            c = [b for b in self.dd_by_amt.get((t.account_id, round(t.amount, 2)), [])
                 if abs(_days(t.booking_date, self.dd_batch[b]["collection_date"].iloc[0])) <= 3]
            if len(c) != 1:
                return False
            bid = c[0]
            g = self.dd_batch[bid]
        if abs(g["amount"].sum() - t.amount) > max(0.02, 0.0001 * t.amount):
            self.stats["dd_amount_mismatch"] += 1
        ent = self.acc_ent.get(t.account_id, t.entity)
        out = self._receipt_rows(g, ent)
        if not out:
            self.stats["dd_unresolved"] += 1
            return False
        self.stats["dd_batch"] += 1
        self.done_batches.add(bid)
        return self.ctx.allocate(t.bank_txn_id, out, "batches:dd")

    def try_dd_return(self, t, txt):
        if t.amount >= 0 or not self.dd_by_mandate:
            return False
        amt = -t.amount
        ent = self.acc_ent.get(t.account_id, t.entity)
        mands = MANDATE_RE.findall(txt)
        cands = []
        # batch id quoted in the return text + amount (+ mandate prefix)
        for tok in ID11_RE.findall(txt):
            g = self._same_acc(self.dd_batch.get(tok), t.account_id, "account_id")
            if g is not None:
                s = g[(g["amount"] - amt).abs() < TOL]
                if mands and "mandate_id" in s.columns:
                    s2 = s[s["mandate_id"].str.upper().str.startswith(tuple(mands))]
                    s = s2 if len(s2) else s
                cands.extend(s.itertuples(index=False))
        if not cands:
            for m in mands:
                for r in self.dd_by_mandate.get(m, []):
                    if abs(float(r.amount) - amt) < TOL and _s(r.collection_date) <= t.booking_date \
                            and _s(getattr(r, "account_id", t.account_id)) == t.account_id:
                        cands.append(r)
            if not cands and not mands:
                return False
        if not cands:
            # mandate known but receipt not in loaded files: customer item with this total, already cleared
            cust = self.ix.mandate.get(mands[0]) if mands else None
            if not cust:
                return False
            iid = self.ix.resolve_ar(ent, "", amt, {cust}, want_open=False, before=t.booking_date)
            if not iid:
                return False
            self.stats["dd_return_nofile"] += 1
            return self.ctx.allocate(t.bank_txn_id, [{"item_id": iid, "amount": amt, "exception": "RETURNED"}],
                                     "batches:ddret")
        # the returned receipt must belong to a batch already credited (processed before this line) or to a batch
        # collected before the window; a batch booked the same day / later cannot be returned yet
        ok = [r for r in cands if _s(r.batch_id) in self.done_batches or _s(r.collection_date) < self.win_start]
        pool = ok or cands
        # a returned receipt is usually re-presented later (re_presentation=Y): prefer the one collected again
        later = {(_s(x.mandate_id).upper(), _s(x.remittance_info)) for m in mands for x in self.dd_by_mandate.get(m, [])
                 if _s(getattr(x, "re_presentation", "")).upper() == "Y" and _s(x.collection_date) > t.booking_date}
        rep = [r for r in pool if (_s(r.mandate_id).upper(), _s(r.remittance_info)) in later]
        r = max(rep or pool, key=lambda r: _s(r.collection_date))
        res = self._receipt_items(r, ent, want_open=False, before=t.booking_date)
        if not res:
            return False
        self.stats["dd_return"] += 1
        return self.ctx.allocate(t.bank_txn_id, [{"item_id": i, "amount": a, "exception": "RETURNED"} for i, a in res],
                                 "batches:ddret")

    # ---------------------------------------------------------------- cards
    def _order_item(self, order, ent, gross, want_open=True):
        # FINAL: card order numbers repeat across entities (WEB26-9000002 exists for UK01 and AU01) -> cache per entity
        key = (order, ent)
        iid = self.order_item.get(key)
        if iid:
            return iid
        c = self.ix.doc_cands(order, {"ARI"}, {ent}) or self.ix.doc_cands(order, {"ARI"}, None)
        if not c:
            return None
        iid = self.ix._best(c, gross, want_open)
        self.order_item[key] = iid
        return iid

    def try_card(self, t, txt):
        if t.amount <= 0 or not self.card_settle:
            return False
        c = [s for s in self.card_by_amt.get((t.account_id, round(t.amount, 2)), [])
             if 0 <= _days(t.booking_date, self.card_settle[s]["settlement_date"].iloc[0]) <= 4]
        if len(c) > 1:
            c2 = [s for s in c if self.card_settle[s]["settlement_date"].iloc[0] == t.booking_date]
            c = c2 or c
        if len(c) != 1:
            return False
        g = self.card_settle[c[0]]
        ent = self.acc_ent.get(t.account_id, t.entity)
        out, seen = [], set()
        for r in g.itertuples(index=False):
            iid = self._order_item(_s(r.order_number), ent, float(r.gross))
            if not iid or iid in seen:
                return False
            seen.add(iid)
            op = self.ix.open_amt(iid)
            gross = float(r.gross)
            if op > 0.005 and gross < op - max(0.01, 0.005 * op):
                out.append({"item_id": iid, "amount": gross, "exception": "PARTIAL"})
            else:
                out.append({"item_id": iid, "amount": op if op > 0.005 else gross,
                            "exception": "" if op > 0.005 else "DUPLICATE_PAYMENT"})
        self.stats["card"] += 1
        return self.ctx.allocate(t.bank_txn_id, out, "batches:card")

    def try_chargeback(self, t, txt):
        if t.amount >= 0 or not self.card_by_mask:
            return False
        amt = -t.amount
        ent = self.acc_ent.get(t.account_id, t.entity)
        if not any(w in txt for w in CB_WORDS):
            return False
        # order number printed directly?
        for tok in re.findall(r"\b([A-Z]{2,4}\d{2}-?\d{5,})\b", txt):
            c = self.ix.doc_cands(tok, {"ARI"}, {ent})
            if c:
                iid = self.ix._best(c, amt, False)
                self.stats["chargeback"] += 1
                return self.ctx.allocate(t.bank_txn_id, [{"item_id": iid, "amount": amt, "exception": "RETURNED"}],
                                         "batches:cb")
        m = CARD_RE.search(txt)
        if not m:
            return False
        cands = [r for r in self.card_by_mask.get(m.group(1), [])
                 if abs(float(r.gross) - amt) < TOL and _s(r.settlement_date) <= t.booking_date
                 and _s(r.account_id) == t.account_id]
        mid = re.findall(r"(?:MID|COMERC)\s*(\d{6,})", txt)
        if mid and len(cands) > 1:
            cands = [r for r in cands if _s(getattr(r, "merchant_id", "")).lstrip("0") in {x.lstrip("0") for x in mid}] or cands
        if not cands:
            return False
        r = max(cands, key=lambda r: _s(r.settlement_date))
        iid = self._order_item(_s(r.order_number), ent, amt, want_open=False)
        if not iid:
            return False
        self.stats["chargeback"] += 1
        return self.ctx.allocate(t.bank_txn_id, [{"item_id": iid, "amount": amt, "exception": "RETURNED"}], "batches:cb")

    # ---------------------------------------------------------------- cheques
    def _cheque_item(self, r, ent, want_open=True, before=None):
        key = (_s(r.deposit_id), _s(r.cheque_number), _s(getattr(r, "account_id", "")))
        if key in self.cheque_item:
            return self.cheque_item[key]
        nm = norm(_s(getattr(r, "drawer", "")))
        parties = self.ix.cust_name.get((ent, nm))
        iid = self.ix.resolve_ar(ent, _s(getattr(r, "stub_reference", "")), float(r.amount), parties,
                                 want_open=want_open, before=before)
        res = [(iid, float(r.amount))] if iid else None
        if res:
            self.cheque_item[key] = res
        return res

    def try_cheque(self, t, txt):
        if t.amount <= 0 or not self.dep:
            return False
        did = None
        if self.dep_re:
            for tok in self.dep_re.findall(txt):
                if tok in self.dep and self._same_acc(self.dep[tok], t.account_id, "account_id") is not None:
                    did = tok
                    break
        if did is None:
            c = [d for d in self.dep_by_amt.get((t.account_id, round(t.amount, 2)), [])
                 if 0 <= _days(t.booking_date, self.dep[d]["deposit_date"].iloc[0]) <= 4]
            if len(c) != 1:
                return False
            did = c[0]
        g = self._same_acc(self.dep[did], t.account_id, "account_id")
        ent = self.acc_ent.get(t.account_id, t.entity)
        out, seen = [], set()
        for r in g.itertuples(index=False):
            res = self._cheque_item(r, ent)
            if not res:
                self.stats["cheque_unresolved"] += 1
                return False
            for iid, a in res:
                if iid in seen:
                    return False
                seen.add(iid)
                op = self.ix.open_amt(iid)
                if op > 0.005 and a < op - max(0.01, 0.005 * op):
                    out.append({"item_id": iid, "amount": a, "exception": "PARTIAL"})
                else:
                    out.append({"item_id": iid, "amount": op if op > 0.005 else a,
                                "exception": "" if op > 0.005 else "DUPLICATE_PAYMENT"})
        self.stats["cheque"] += 1
        return self.ctx.allocate(t.bank_txn_id, out, "batches:chq")

    def try_cheque_return(self, t, txt):
        if t.amount >= 0 or not self.chq_by_num:
            return False
        amt = -t.amount
        ent = self.acc_ent.get(t.account_id, t.entity)
        for num in CHQ_RE.findall(txt):
            cands = [r for r in self.chq_by_num.get(num.lstrip("0"), [])
                     if abs(float(r.amount) - amt) < TOL and _s(r.deposit_date) <= t.booking_date]
            if not cands:
                continue
            r = max(cands, key=lambda r: _s(r.deposit_date))
            res = self._cheque_item(r, ent, want_open=False, before=t.booking_date)
            if not res:
                continue
            self.stats["cheque_return"] += 1
            return self.ctx.allocate(t.bank_txn_id, [{"item_id": i, "amount": a, "exception": "RETURNED"} for i, a in res],
                                     "batches:chqret")
        return False

    # ---------------------------------------------------------------- driver
    def run(self):
        handlers = (self.try_run, self.try_transfer_return, self.try_dd_batch, self.try_dd_return, self.try_card, self.try_chargeback,
                    self.try_cheque, self.try_cheque_return)
        cols = ["bank_txn_id", "account_id", "entity", "booking_date", "amount", "currency", "text", "ref1", "ref2",
                "counterparty_name", "remittance_info", "end_to_end_id", "orig_amount", "orig_currency"]
        pend = self.ctx.pending()
        cols = [c for c in cols if c in pend.columns]
        for t in pend[cols].itertuples(index=False):
            txt = " | ".join(_s(getattr(t, k, "")) for k in ("text", "ref1", "ref2", "counterparty_name",
                                                           "remittance_info", "end_to_end_id")).upper()
            for h in handlers:
                try:
                    if h(t, txt):
                        break
                except Exception as e:  # noqa: BLE001
                    self.stats["err_" + h.__name__] += 1
                    if self.stats["err_" + h.__name__] <= 3:
                        print(f"    [batches] {h.__name__} {t.bank_txn_id}: {type(e).__name__} {e}")
        print("    [batches] " + ", ".join(f"{k}={v}" for k, v in sorted(self.stats.items())))


def run(ctx):
    Batches(ctx).run()
