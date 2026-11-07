import os
"""AR receipts by transfer: identify the payer, parse references, explain the amount over the customer's open items.

See POLICIES §3, §5. No labels used. Order: lines are processed chronologically (ledger FIFO).
"""
import os
import re
from collections import defaultdict
from datetime import date as _date, timedelta

from data import norm, norm_words
from ar_index import ARIndex, IBAN_RE, AMT_RE, SPLIT_RE, core_name, find_month, LEGAL

CONNECT = ("Y", "AND", "ET", "UND", "E", "I")
SEPA_EUR = {"ES", "PT", "FR", "IT", "DE", "NL", "BE", "AT", "IE", "LU", "FI", "GR", "MT", "CY", "SK", "SI", "EE", "LV",
            "LT", "HR"}
WH_COUNTRIES = {"MX", "BR", "MA"}
WH_RATES = (0.0125, 0.015, 0.10)
MAX_NODES = 6000
ZERO_UNITS = True
SUBSET_ALL = False
DP_MAX_POOL = 80
FRACTIONS = (0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.9, 0.95)
STRICT_WEAK = True
STRICT_GAP = 0.3
NM_FRACTIONS = (0.4, 0.5, 0.6)   # n-to-m: one invoice of a multi-invoice transfer paid at a clean fraction
NM_ON = os.environ.get("AR_NM", "1") == "1"
NM_MAX_POOL = 40
NM_COST = 3e10
NM_MAX_ITEMS = 4   # without references: chance exact-cent combinations grow fast with the set size
SETTLE_WORDS = {"SETTLEMENT", "REGULARIZACION", "REGULARISATION", "REGOLARIZZAZIONE", "REGULARIZACAO"}
INST_RE = re.compile(r"(?<![\d/])(\d{1,2})\s?/\s?(\d{1,2})(?![\d/])")

PARTIAL_WORDS = {"CUENTA", "ACOMPTE", "ACCONTO", "PARTIAL", "TEILZAHLUNG", "ABSCHLAG", "PARCIAL", "INSTALMENT",
                 "INSTALLMENT", "RATE", "ECHEANCE", "PLAZO", "VENCIMIENTO", "RATA", "PARCELA", "RATY", "TERMIJN",
                 "BALANCE"}


def _d(s):
    return _date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def cents(x):
    return int(round(x * 100))


class Matcher:
    def __init__(self, ctx):
        self.ctx = ctx
        self.ix = ARIndex(ctx)
        if os.environ.get("PAYER_DIR", "0") == "1":
            import payer_dir
            payer_dir.apply(ctx, self.ix)
        self.open = ctx.open
        self.acct_ccy = dict(zip(ctx.masters["bank_accounts"].account_id, ctx.masters["bank_accounts"].currency))
        self.stats = defaultdict(int)
        self._hz = {}
        self.closed_by = {}
        self.load_pdf(ctx)
        self._zlo = ""
        self.attach = {}
        self.zero_units = []

    def load_pdf(self, ctx):
        """Optional PDF clause info (src/pdfinfo.py): instalments, retention, advance, service month, replaced
        invoices, new IBAN notices. Everything degrades to empty dicts when the caches are missing."""
        self.p_inst, self.p_ret, self.p_adv, self.p_month, self.p_repl = {}, {}, {}, {}, {}
        try:
            import pdfinfo as P
            self.p_inst = P.instalments(ctx) or {}
            self.p_ret = P.retention(ctx) or {}
            self.p_adv = P.advance(ctx) or {}
            self.p_month = P.service_month(ctx) or {}
            self.p_repl = P.replaced_by(ctx) or {}
            P.new_ibans(ctx)
            for ib, pid in (ctx.cache.get("pdfinfo_iban_party") or {}).items():
                if pid in self.ix.cust:
                    self.ix.iban[norm(ib)].add(pid)
        except Exception as e:  # noqa: BLE001
            print("   ar: pdf info unavailable:", e)

    def pdf_partial(self, O, R, A, tol, fx):
        """First payments the invoice PDF announces: instalments, payable net of retention, net of advance."""
        it = self.ix.item
        tl = max(tol, 0.011)
        Ri = [i for i in R if it[i][0] == "ARI"]
        inv = [i for i in O if it[i][0] == "ARI"]
        for pool in (Ri, inv):
            for i in pool:
                o = self.sv(i)
                exp = []
                pi = self.p_inst.get(i)
                if pi:
                    exp += list(pi.get("amounts") or [])
                pr = self.p_ret.get(i)
                if pr and pr.get("payable"):
                    exp.append(pr["payable"])
                pa = self.p_adv.get(i)
                if pa and pa.get("net_due"):
                    exp.append(pa["net_due"])
                for x in exp:
                    if x and abs(x - A) <= tl:
                        if o > A + tl:
                            return self.rows_for([i], A, fx, {i: "PARTIAL"}, {i: A}), "pdf_partial"
                        if abs(o - A) <= tl:
                            return self.rows_for([i], A, fx), "pdf_last"
        return None

    # ------------------------------------------------------------ payer identification
    def text_words(self, t):
        parts = [t.text, t.counterparty_name, t.remittance_info, t.ref2]
        s = " | ".join(p for p in parts if isinstance(p, str) and p)
        return s

    def names(self, entity, words):
        """Return {cust_id: score} from fuzzy prefix matching of aliases against the text words."""
        T = " ".join(words)
        # char offsets of each word
        offs, p = [], 0
        for w in words:
            offs.append(p)
            p += len(w) + 1
        found = {}
        for k, w in enumerate(words):
            lst = self.ix.alias.get((entity, w))
            if not lst:
                continue
            p0 = offs[k]
            for core, cid, kind, tail in lst:
                n = len(core)
                seg = T[p0:p0 + n + 1]
                L = 0
                m = min(n, len(seg))
                while L < m and seg[L] == core[L]:
                    L += 1
                end_ok = (p0 + L >= len(T)) or T[p0 + L] == " "
                if L == n and end_ok:
                    sc = 1.0
                    # legal-form conflict (lookalike payer): text continues with a different legal form
                    if tail:
                        rest = T[p0 + L:].split()[:4]
                        tt = []
                        for x in rest:
                            if x in LEGAL or len(x) == 1:
                                tt.append(x)
                            else:
                                break
                        tt = "".join(tt)
                        if tt and tt != tail and not tail.startswith(tt) and not tt.startswith(tail):
                            sc = 0.3
                elif L >= 10 and end_ok and L < n:
                    sc = 0.5 + 0.4 * L / n
                else:
                    continue
                if kind == 2:
                    sc -= 0.02
                if sc > found.get(cid, 0):
                    found[cid] = sc
        return found

    # ------------------------------------------------------------ references
    def tokens(self, s):
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
        # also join adjacent fragments like "FV | 23-0303705" -> handled by digits key
        return toks

    def global_refs(self, entity, toks):
        hits = defaultdict(int)   # item -> strength
        for tk in toks:
            for iid in self.ix.doc.get((entity, tk), ()):
                hits[iid] = max(hits[iid], 3)
            dg = re.sub(r"\D", "", tk).lstrip("0")
            if len(dg) >= 7:
                for iid in self.ix.doc.get((entity, "#" + dg), ()):
                    hits[iid] = max(hits[iid], 3)
            for iid in self.ix.po.get((entity, tk), ()):
                hits[iid] = max(hits[iid], 2)
        return hits

    def cust_refs(self, items, toks):
        """Fuzzy reference matching restricted to one customer's items. Returns {item: strength}."""
        out = {}
        if not toks:
            return out
        tdig = []
        for tk in toks:
            dg = re.sub(r"\D", "", tk)
            tdig.append((tk, dg.lstrip("0"), dg))
        for iid in items:
            v = self.ix.item[iid]
            nd, dg, po = v[11], v[12], v[13]
            best = 0
            for tk, d, draw in tdig:
                if tk == nd or (d and d == dg) or (po and tk == po):
                    best = 3
                    break
                if len(draw) >= 4 and dg.endswith(draw) and len(draw) >= min(5, len(dg)) - 1:
                    best = max(best, 2)
                elif len(d) >= 6 and dg.startswith(d):
                    best = max(best, 2)
                elif len(d) >= 6 and len(d) == len(dg) and _dist1(d, dg):
                    best = max(best, 1)
                elif len(draw) >= 5 and len(draw) < len(dg) and _dist1(draw, dg[-len(draw):]):
                    best = max(best, 1)
            if best:
                out[iid] = best
        return out

    # ------------------------------------------------------------ amount logic
    def item_cash_variants(self, iid, op, bdate):
        """Alternative cash values for an invoice: [(cash, exception, row_amount)]."""
        v = self.ix.item[iid]
        out = []
        if v[0] != "ARI":
            return out
        tot = v[7]
        pct, dd = self.ix.terms.get(v[8], (0.0, 0))
        if pct and abs(op - tot) < 0.005:
            cash = round(tot - round(tot * pct / 100.0, 2), 2)
            if bdate <= v[4] and False:
                pass
            lim = (_d(v[4]) + timedelta(days=dd + 3)).isoformat()
            if bdate <= lim:
                out.append((cash, "SHORT_DISCOUNT", op))
            else:
                out.append((cash, "PARTIAL", cash))
        return out

    def run_line(self, t):
        self.budget = MAX_NODES
        ent = t.entity
        bdate = t.booking_date
        A = float(t.amount)
        s = self.text_words(t)
        words = [w for w in norm_words(s).split() if w not in CONNECT]
        toks = self.tokens(s)
        ccy = t.currency

        # --- candidate customers
        cand = defaultdict(float)
        why = defaultdict(set)
        grefs = self.global_refs(ent, toks)
        for iid, st in grefs.items():
            pid = self.ix.item[iid][2]
            cand[pid] = max(cand[pid], 2.0 + st / 10)
            why[pid].add("ref")
        ibans = set(IBAN_RE.findall(s.upper()))
        if isinstance(t.counterparty_account, str) and t.counterparty_account:
            ibans.add(norm(t.counterparty_account))
        for ib in ibans:
            for cid in self.ix.iban.get(norm(ib), ()):
                if cid in self.ix.cust and self.ix.cust[cid].entity == ent:
                    cand[cid] = max(cand[cid], 2.5)
                    why[cid].add("iban")
        for cid, sc in self.names(ent, words).items():
            cand[cid] = max(cand[cid], 1.0 + sc)
            why[cid].add("name")
        if not cand:
            self.stats["no_cand"] += 1
            return None
        # parent pays for subsidiaries: add group members (weaker)
        for cid in list(cand):
            r = self.ix.cust.get(cid)
            if r is not None and r.group_id:
                for o in self.ix.group.get((ent, r.group_id), ()):
                    if o not in cand:
                        cand[o] = cand[cid] - 1.0
                        why[o].add("group")
        order = sorted(cand, key=lambda c: -cand[c])[:8]
        self.words_set = set(words)
        self.pre = {}
        for cid in order:
            self.pre[cid] = self.cust_ctx(cid, t, bdate, toks, grefs)
        def rk(c):
            refs = self.pre[c][1]
            strong = sum(1 for v in refs.values() if v >= 2)
            return (-min(strong, 1), -cand[c], -strong)
        order.sort(key=rk)
        month = find_month(words)
        self.inst = None
        m = INST_RE.search(" ".join(words[:0]) or s.upper())
        if m:
            k, n = int(m.group(1)), int(m.group(2))
            if 1 <= k <= n <= 24 and n >= 2:
                self.inst = (k, n)
        top = max(cand.values())
        for cid in order:
            weak = "group" in why[cid] and len(why[cid]) == 1
            # weaker name-only candidate (lookalike legal form / truncated name) while a better-identified customer
            # exists: only accept exact evidence (referenced set or single exact invoice), no combinatorial guesses
            if not weak and STRICT_WEAK and not (why[cid] & {"ref", "iban"}) and cand[cid] < top - STRICT_GAP:
                weak = True
            res = self.try_customer(cid, t, A, ccy, bdate, toks, grefs, month, cand[cid], strict=weak)
            if res:
                return res
        self.stats["cand_nomatch"] += 1
        return None

    def horizon(self, bdate):
        h = self._hz.get(bdate)
        if h is None:
            h = self._hz[bdate] = (_d(bdate) - timedelta(days=200)).isoformat()
        return h

    def amount_in(self, A, ccy, icur, t, bdate):
        """Bank amount expressed in item currency: (value, tolerance, fx_flag)."""
        if icur == ccy:
            return A, 0.005, False
        if isinstance(t.orig_currency, str) and t.orig_currency == icur and t.orig_amount == t.orig_amount:
            return float(t.orig_amount), 0.005, True
        v = self.ix.fx.conv(A, ccy, icur, bdate)
        if v is None:
            return None, 0, True
        return v, 0.03 * v, True

    def cust_ctx(self, cid, t, bdate, toks, grefs):
        lo = self.horizon(bdate)
        allit = [i for i in self.ix.cust_items.get(cid, ()) if self.ix.item[i][1] == t.entity
                 and (i in self.open or self.ix.item[i][4] >= lo)]
        refs = self.cust_refs(allit, toks) if allit else {}
        for i, st in grefs.items():
            if self.ix.item[i][2] == cid:
                refs[i] = max(refs.get(i, 0), st)
        if self.p_repl:
            for i in list(refs):
                n = self.p_repl.get(i)
                if n and n in self.ix.item:
                    refs[n] = max(refs.get(n, 0), refs[i])
        return allit, refs

    def try_customer(self, cid, t, A0, ccy, bdate, toks, grefs, month, evid, strict=False):
        allit, refs = self.pre[cid]
        if not allit:
            return None
        opn = [i for i in allit if self.open.get(i, 0) > 0.005 and self.ix.item[i][4] <= bdate]
        opn = self.make_units(opn, t.entity)
        self._zlo = (_d(bdate) - timedelta(days=90)).isoformat()
        # group open items by currency
        bycur = defaultdict(list)
        for i in opn:
            bycur[self.ix.item[i][6]].append(i)
        curs = sorted(bycur, key=lambda c: (c != ccy, c))
        if not curs and refs:
            curs = [self.ix.item[next(iter(refs))][6]]
        for icur in curs:
            A, tol, fx = self.amount_in(A0, ccy, icur, t, bdate)
            if A is None:
                continue
            O = bycur.get(icur, [])
            R = [i for i in O if refs.get(i)]
            res = self.explain(cid, O, R, refs, A, tol, fx, bdate, month, t, evid, strict)
            if res:
                return res
            # referenced but already settled item paid again -> duplicate
            Rc = [i for i in refs if self.open.get(i, 0) <= 0.005 and self.ix.item[i][6] == icur
                  and self.ix.item[i][0] == "ARI" and refs[i] >= 2]
            for i in Rc:
                if abs(self.ix.item[i][7] - A) <= max(tol, 0.005):
                    how = self.closed_by.get(i)
                    if how is not None and not how.startswith("ref") and how != "dup_ref":
                        # our own earlier non-referenced claim was probably wrong: this line pays it
                        return self.rows_for([i], A, fx, amt_map={i: self.ix.item[i][7]}), "ref_reclaim"
                    return [{"item_id": i, "amount": A, "exception": "DUPLICATE_PAYMENT"}], "dup_ref"
        return None

    def make_units(self, opn, ent):
        """Drop cancelled invoices (+ their cancelling credit note); attach return credit notes to their invoice."""
        it = self.ix.item
        dmap = self.ix.doc_by_num
        S = set(opn)
        drop = set()
        attach = defaultdict(list)
        for i in opn:
            v = it[i]
            if v[0] != "ARC" or not v[10]:
                continue
            rel = dmap.get((ent, v[10]))
            if v[14] == "C":
                drop.add(i)
                if rel:
                    drop.add(rel)
            elif v[14] != "R" and rel in S:
                attach[rel].append(i)
                drop.add(i)
        self.attach = attach
        self.zero_units = []
        out = []
        for i in opn:
            if i in drop:
                continue
            if i in attach and self.sv(i) <= 0.005:
                self.zero_units.append(i)
                continue
            out.append(i)
        return out

    def rows_for(self, sel, A, fx, exc_map=None, amt_map=None):
        rows = []
        ex = []
        for i in sel:
            ex.append(i)
            ex.extend(self.attach.get(i, ()))
        if ZERO_UNITS and self.zero_units:
            for i in self.zero_units:
                if self.ix.item[i][4] < self._zlo:
                    continue
                ex.append(i)
                ex.extend(self.attach.get(i, ()))
        for i in ex:
            v = self.ix.item[i]
            op = self.open.get(i, 0.0)
            e = (exc_map or {}).get(i, "")
            a = (amt_map or {}).get(i, op)
            if fx and v[0] == "ARI" and e in ("", "SHORT_DISCOUNT", "SHORT_WITHHOLDING", "SHORT_BANK_CHARGES",
                                                "OVERPAYMENT"):
                e = "FX_DIFFERENCE"
            if v[0] == "ARC":
                e = ""
            rows.append({"item_id": i, "amount": a, "exception": e})
        return rows

    def explain(self, cid, O, R, refs, A, tol, fx, bdate, month, t, evid, strict=False):
        it = self.ix.item
        op = self.open
        ctry = getattr(self.ix.cust.get(cid), "country", "") or ""
        # --- settlement of a previously disputed remainder ("SETTLEMENT FA24...", "REGULARIZACION ..."):
        # the cited invoice gets the cash with no exception (POLICIES §3), whatever our ledger still shows open
        if R and self.words_set & SETTLE_WORDS:
            Ri = [i for i in R if it[i][0] == "ARI" and refs.get(i, 0) >= 2]
            if len(Ri) == 1 and self.sv(Ri[0]) > A + tol:
                i = Ri[0]
                return self.rows_for([i], A, fx, {i: ""}, {i: A}), "settle"
        if self.p_inst or self.p_ret or self.p_adv:
            r = self.pdf_partial(O, R, A, tol, fx)
            if r:
                return r
        # --- 0. instalment plans ("PLAZO 2/4", "RATE 1/3" ...)
        r = self.instalment(O, R, A, tol, fx, bdate)
        if r:
            return r
        # --- 1. referenced set exact
        if R:
            Rs = sorted(R, key=lambda i: (it[i][4], it[i][3]))
            s = sum(self.sv(i) for i in Rs)
            if abs(s - A) <= tol:
                return self.rows_for(Rs, A, fx), "ref_exact"
            # referenced invoices + open credit notes of the customer
            cn = [i for i in O if it[i][0] == "ARC" and i not in R]
            if cn:
                sub = self.subset(cn, A - s, tol, prefer=())
                if sub is not None:
                    return self.rows_for(Rs + sub, A, fx), "ref_cn"
            # variants on the referenced set
            r = self.variants(Rs, A, tol, fx, bdate, ctry, t)
            if r:
                return r, "ref_var"
        # --- 2. single open invoice with exact amount
        same = [i for i in O if abs(self.sv(i) - A) <= tol and it[i][0] == "ARI"]
        if same:
            pick = self.pick_same(same, refs, month)
            return self.rows_for([pick], A, fx), "single"
        if strict:
            return None
        # --- 2b. referenced invoices + some unreferenced ones (truncated reference lists)
        if R:
            sR = sum(self.sv(i) for i in R)
            rest = [i for i in O if i not in set(R)]
            if sR < A - tol and rest:
                sub = self.subset_all(rest, A - sR, tol, {}, bdate)
                if sub is not None:
                    Rs = sorted(R, key=lambda i: (it[i][4], it[i][3]))
                    return self.rows_for(Rs + sub[0], A, fx), "refplus_" + sub[1]
        # --- 3. subset sum over open items (references preferred, oldest first)
        if len(O) >= 2:
            sub = self.subset_all(O, A, tol, refs, bdate)
            if sub is not None:
                return self.rows_for(sub[0], A, fx), "subset_" + sub[1]
        # --- 4. variants over single items / subsets
        r = self.variants_search(O, R, A, tol, fx, bdate, ctry, t, refs)
        if r:
            return r, "var"
        # --- 4b. untouched invoice paid by a round fraction (partial / retention / advance net)
        if evid >= 1.5:
            r = self.fraction_partial(O, R, A, tol, fx, bdate)
            if r:
                return r
        # --- 4c. n-to-m: open balances in full (remainders at their open amount) + ONE untouched invoice at a clean
        # fraction of its total (PARTIAL). Referenced items mandatory when present; the solution must be unique.
        if NM_ON and evid >= 1.5 and tol <= 0.0051 and len(O) >= 2:
            r = self.nm_fraction(O, R, refs, A, fx, bdate)
            if r:
                return r
        # --- 5. single referenced invoice: partial / overpayment
        Ri = [i for i in R if it[i][0] == "ARI"]
        if len(Ri) == 1 and evid >= 1.5:
            i = Ri[0]
            o = self.sv(i)
            if A > o + tol and refs[i] >= 3 and A - o <= max(0.1 * o, 1.0):
                return self.rows_for([i], A, fx, {i: "OVERPAYMENT"}), "over"
        return None

    def instalment(self, O, R, A, tol, fx, bdate):
        it = self.ix.item
        inv = [i for i in O if it[i][0] == "ARI"]
        Ri = [i for i in R if it[i][0] == "ARI"]
        tl = max(tol, 0.011)
        if self.inst:
            k, n = self.inst
            for pool in (Ri, inv):
                hits = []
                for i in pool:
                    tot, o = it[i][7], self.sv(i)
                    if abs(round(tot / n, 2) - A) <= tl + 0.01 or (k == n and abs(o - A) <= tl) \
                            or (k == n and abs(tot - round(tot / n, 2) * (n - 1) - A) <= tl):
                        if o >= A - tl:
                            hits.append(i)
                if hits:
                    i = min(hits, key=lambda x: (it[x][4], it[x][3]))
                    e = "PARTIAL" if k < n else ""
                    return self.rows_for([i], A, fx, {i: e}, {i: A}), "inst"
            return None
        # unplanned text: referenced invoice paid exactly 1/n of its total
        for i in Ri:
            tot, o = it[i][7], self.sv(i)
            if o > A + tl:
                for n in (2, 3, 4, 5, 6):
                    if abs(round(tot / n, 2) - A) <= tl:
                        return self.rows_for([i], A, fx, {i: "PARTIAL"}, {i: A}), "inst_ref"
        return None

    def nm_fraction(self, O, R, refs, A, fx, bdate):
        it = self.ix.item
        lo = (_d(bdate) - timedelta(days=400)).isoformat()
        hi = (_d(bdate) + timedelta(days=20)).isoformat()
        T = cents(A)
        pool = [i for i in O if it[i][0] == "ARI" and self.sv(i) > 0.005 and not self.attach.get(i)
                and ((it[i][4] >= lo and it[i][5] <= hi) or refs.get(i))]
        if len(pool) < 2:
            return None
        if len(pool) > NM_MAX_POOL:
            pref = [i for i in pool if refs.get(i)]
            if not pref:
                return None
            rest = sorted((i for i in pool if not refs.get(i)), key=lambda i: (it[i][5], it[i][4]))
            pool = pref + rest[:max(0, NM_MAX_POOL - len(pref))]
        val = {i: cents(self.sv(i)) for i in pool}
        if len(pool) * T > 4e8:
            return None
        fr0 = sum(1 for i in pool if abs(self.open[i] - it[i][7]) < 0.005)
        if 3 * fr0 * len(pool) * T > NM_COST:
            self.stats["nm_cost"] += 1
            return None
        Rset = [i for i in R if i in val]
        fr = [i for i in pool if abs(self.open[i] - it[i][7]) < 0.005]   # untouched invoices
        for mand in ((Rset, "nm_ref"), ([], "nm")) if Rset else (([], "nm"),):
            mand, kind = mand
            sols = []
            for p in fr:
                tot = cents(it[p][7])
                for f in NM_FRACTIONS:
                    part = int(round(tot * f))
                    m_ = [i for i in mand if i != p]
                    tgt = T - part - sum(val[i] for i in m_)
                    if tgt < 0:
                        continue
                    rest = [i for i in pool if i != p and i not in m_]
                    if tgt == 0:
                        sub = []
                    else:
                        sub = self._dp_one(rest, val, tgt)
                        if sub is None:
                            continue
                    if not m_ and not sub:
                        continue
                    sols.append((p, part, m_ + sub, rest, tgt))
                    if len(sols) > 1:
                        break
                if len(sols) > 1:
                    break
            if len(sols) == 1:
                p, part, sel, rest, tgt = sols[0]
                if kind == "nm" and len(sel) + 1 > NM_MAX_ITEMS:
                    self.stats["nm_big"] += 1
                    return None
                # uniqueness of the subset: no solution may avoid any selected (non-mandatory) item
                free = [i for i in sel if i not in mand]
                for q in free:
                    if self._dp_one([i for i in rest if i != q], val, tgt) is not None:
                        return None
                sel = sorted(sel, key=lambda i: (it[i][4], it[i][3]))
                return self.rows_for(sel + [p], A, fx, {p: "PARTIAL"}, {p: part / 100.0}), kind
            if sols:
                return None
        return None

    def _dp_one(self, items, val, tgt):
        """One exact subset (bitset DP) of items with integer values val summing to tgt, or None."""
        if tgt <= 0:
            return [] if tgt == 0 else None
        mask = (1 << (tgt + 1)) - 1
        reach = [1]
        r = 1
        for i in items:
            r = (r | (r << val[i])) & mask
            reach.append(r)
        if not (r >> tgt) & 1:
            return None
        sel, cur = [], tgt
        for k in range(len(items), 0, -1):
            if (reach[k - 1] >> cur) & 1:
                continue
            sel.append(items[k - 1])
            cur -= val[items[k - 1]]
        return sel if cur == 0 else None

    def fraction_partial(self, O, R, A, tol, fx, bdate):
        it = self.ix.item
        tl = max(tol, 0.011)
        lo = (_d(bdate) - timedelta(days=400)).isoformat()
        inv = [i for i in O if it[i][0] == "ARI" and it[i][4] >= lo and abs(self.open[i] - it[i][7]) < 0.005]
        hits = []
        for i in inv:
            tot = it[i][7]
            for f in FRACTIONS:
                if abs(round(tot * f, 2) - A) <= tl:
                    hits.append(i)
                    break
        if not hits:
            return None
        Rs = set(R)
        i = min(hits, key=lambda x: (0 if x in Rs else 1, it[x][5], it[x][4]))
        return self.rows_for([i], A, fx, {i: "PARTIAL"}, {i: A}), "frac_partial"

    def sv(self, i):
        v = self.open.get(i, 0.0)
        if self.ix.item[i][0] == "ARC":
            return -v
        for c in self.attach.get(i, ()):
            v -= self.open.get(c, 0.0)
        return v

    def pick_same(self, same, refs, month):
        it = self.ix.item
        r = [i for i in same if refs.get(i)]
        if r:
            return max(r, key=lambda i: (refs[i], -int(it[i][4].replace("-", ""))))
        if month and len(same) > 1:
            m, y = month
            pm = [i for i in same if i in self.p_month and int(self.p_month[i][5:7]) == m
                  and (y is None or int(self.p_month[i][:4]) == y)]
            if pm:
                return min(pm, key=lambda i: it[i][4])
            cands = []
            for i in same:
                d = it[i][4]
                im, iy = int(d[5:7]), int(d[:4])
                sm, sy = (im - 1, iy) if im > 1 else (12, iy - 1)
                if sm == m and (y is None or y == sy):
                    cands.append(i)
            if cands:
                return min(cands, key=lambda i: it[i][4])
        return min(same, key=lambda i: (it[i][4], it[i][3]))

    # ------------------------------------------------------------ subset search
    def subset(self, items, target, tol, prefer=()):
        """Find a subset of items (signed open values) summing to target within tol. Oldest-first DFS."""
        it = self.ix.item
        if not items:
            return None
        vals = [(i, cents(self.sv(i))) for i in items]
        T = cents(target)
        tl = max(1, int(round(tol * 100)))
        pos = [x for x in vals if x[1] > 0]
        neg = [x for x in vals if x[1] < 0]
        pos.sort(key=lambda x: (0 if x[0] in prefer else 1, it[x[0]][4], it[x[0]][3]))
        neg.sort(key=lambda x: it[x[0]][4])
        pos = pos[:30]
        neg = neg[:4]
        from itertools import combinations
        negsets = [()]
        for k in range(1, min(3, len(neg)) + 1):
            negsets += list(combinations(neg, k))
        for ns in negsets:
            tgt = T - sum(x[1] for x in ns)
            r = self._dfs(pos, tgt, tl)
            if r is not None:
                return [x[0] for x in ns] + r
        return None

    def _dfs(self, pos, tgt, tl):
        n = len(pos)
        if n == 0 or tgt <= 0:
            return None
        suffix = [0] * (n + 1)
        for k in range(n - 1, -1, -1):
            suffix[k] = suffix[k + 1] + pos[k][1]
        if suffix[0] < tgt - tl:
            return None
        nodes = [0]
        chosen = []

        def rec(k, rem):
            self.budget -= 1
            if self.budget <= 0:
                return False
            if abs(rem) < tl:
                return True
            if k >= n or rem < 0 or suffix[k] < rem - tl:
                return False
            chosen.append(pos[k][0])
            if rec(k + 1, rem - pos[k][1]):
                return True
            chosen.pop()
            return rec(k + 1, rem)

        if rec(0, tgt):
            return list(chosen)
        return None

    def subset_all(self, O, A, tol, refs, bdate):
        it = self.ix.item
        key = lambda i: (it[i][5], it[i][4], it[i][3])
        lo = (_d(bdate) - timedelta(days=400)).isoformat()
        hi = (_d(bdate) + timedelta(days=20)).isoformat()
        prim = [i for i in O if (it[i][4] >= lo and it[i][5] <= hi) or refs.get(i)]
        T, tl = cents(A), max(1, int(round(tol * 100)))
        for pn, pool in (("prim", prim),):
            for k_ in (key, lambda i: (it[i][4], it[i][3])):
                inv = sorted(pool, key=k_)
                acc = 0
                for k, i in enumerate(inv):
                    acc += cents(self.sv(i))
                    if abs(acc - T) < tl and k >= 1:
                        return inv[:k + 1], "pfx_" + pn
        for pn, pool in (("prim", prim), ("all", O)):
            if len(pool) < 2:
                continue
            if pn == "all" and not SUBSET_ALL:
                break
            n = len(pool)
            bk = "s" if n <= 12 else ("m" if n <= 30 else "l")
            pool = sorted(pool, key=key)
            if tl <= 1 and n <= DP_MAX_POOL and (n <= 30 or any(refs.get(i) for i in pool)):
                r = self.dp_subset(pool, T, refs)
                if r is not None:
                    return r, "dp_%s_%s" % (pn, bk)
            if tl <= 1:
                continue   # exact search already done by DP / MITM; DFS only for FX tolerance
            if len(pool) > 30:
                pref = [i for i in pool if refs.get(i)]
                pool = pref + [i for i in pool if not refs.get(i)][:max(0, 30 - len(pref))]
            r = self.subset(pool, A, tol, prefer=set(i for i in pool if refs.get(i)))
            if r is not None:
                return r, "dfs_%s_%s" % (pn, bk)
        return None

    def dp_subset(self, pool, T, refs):
        """Exact subset sum with big-int bitsets; prefers earlier units (pool order) and referenced ones."""
        it = self.ix.item
        pos = [(i, cents(self.sv(i))) for i in pool if self.sv(i) > 0.005]
        neg = [(i, -cents(self.sv(i))) for i in pool if self.sv(i) < -0.005][:3]
        pos = sorted(pos, key=lambda x: 0 if refs.get(x[0]) else 1)  # stable: refs first, then due order
        from itertools import combinations
        negsets = [()]
        for k in range(1, len(neg) + 1):
            negsets += list(combinations(neg, k))
        for ns in negsets:
            tgt = T + sum(x[1] for x in ns)
            if tgt <= 0:
                continue
            if len(pos) * tgt > 4e8:
                if len(pos) <= 24:
                    r = _mitm(pos, tgt)
                    if r is not None:
                        return [x[0] for x in ns] + r
                continue
            mask = (1 << (tgt + 1)) - 1
            reach = [1]
            r = 1
            for _, v in pos:
                r = (r | (r << v)) & mask
                reach.append(r)
            if not (r >> tgt) & 1:
                continue
            sel, cur = [], tgt
            for k in range(len(pos), 0, -1):
                if (reach[k - 1] >> cur) & 1:
                    continue
                sel.append(pos[k - 1][0])
                cur -= pos[k - 1][1]
            if cur == 0:
                return [x[0] for x in ns] + sel[::-1]
        return None

    # ------------------------------------------------------------ exception variants
    def variants(self, S, A, tol, fx, bdate, ctry, t):
        """Explain cash A against the fixed set S with discounts / withholding / bank charges."""
        it = self.ix.item
        op = self.open
        full = sum(self.sv(i) for i in S)
        # discount variant: (a) only invoices still within the discount window, (b) every discount invoice
        for late_ok in (False, True):
            exc, amt, cash = {}, {}, 0.0
            anyd = False
            for i in S:
                vs = self.item_cash_variants(i, op[i], bdate)
                if vs and (late_ok or vs[0][1] != "PARTIAL"):
                    c, e, a = vs[0]
                    exc[i], amt[i] = e, a
                    cash += c - (op[i] - self.sv(i))
                    anyd = True
                else:
                    cash += self.sv(i)
            if anyd and abs(cash - A) <= tol:
                return self.rows_for(S, A, fx, exc, amt)
        # withholding
        if ctry in WH_COUNTRIES or t.entity[:2] in WH_COUNTRIES:
            for r in WH_RATES:
                cash = 0.0
                for i in S:
                    if it[i][0] == "ARI":
                        tot = it[i][7]
                        cash += self.sv(i) - round(tot * r, 2)
                    else:
                        cash -= op[i]
                if abs(cash - A) <= max(tol, 0.011 * len(S)):
                    return self.rows_for(S, A, fx, {i: "SHORT_WITHHOLDING" for i in S})
        # bank charges (non-SEPA cross-border)
        short = full - A
        if short > tol and len([i for i in S if it[i][0] == "ARI"]) == 1:
            eur = self.ix.fx.conv(short, it[S[0]][6], "EUR", bdate) or short
            if eur <= 60.0 + 1e-6 and short <= 0.02 * full + 1e-6 and self.cross_border(t, ctry):
                return self.rows_for(S, A, fx, {i: "SHORT_BANK_CHARGES" for i in S})
        return None

    def cross_border(self, t, ctry):
        ec = self.ix.ent_country.get(t.entity, "")
        if ctry and ctry == ec:
            return False
        code = (t.bank_code or "") + " " + (t.text or "")
        if "XBCT" in code or "SHA" in code.split() or "BEN" in code.split() or "DIV" in code:
            return True
        if ctry and ctry not in SEPA_EUR and ctry != ec:
            return True
        return False

    def variants_search(self, O, R, A, tol, fx, bdate, ctry, t, refs):
        it = self.ix.item
        op = self.open
        inv = [i for i in O if it[i][0] == "ARI"]
        # single invoice with discount / withholding / bank charges
        cands = []
        for i in inv:
            o = self.sv(i)
            if o < A - tol or o > A * 1.12 + 1:
                continue
            r = self.variants([i], A, tol, fx, bdate, ctry, t)
            if r:
                cands.append((0 if refs.get(i) else 1, it[i][4], r))
        if cands:
            cands.sort(key=lambda x: (x[0], x[1]))
            return cands[0][2]
        # subsets with discount on every discount-term invoice
        lo = (_d(bdate) - timedelta(days=400)).isoformat()
        hi = (_d(bdate) + timedelta(days=20)).isoformat()
        P = [i for i in O if (it[i][4] >= lo and it[i][5] <= hi) or refs.get(i)]
        P.sort(key=lambda i: (it[i][5], it[i][4], it[i][3]))
        for late_ok in (False, True):
            if not (2 <= len(P) <= 40):
                break
            dvals = {}
            for i in P:
                vs = self.item_cash_variants(i, op[i], bdate)
                dvals[i] = vs[0] if vs and (late_ok or vs[0][1] != "PARTIAL") else None
            if any(dvals.values()):
                saved = {}
                for i, v in dvals.items():
                    if v:
                        saved[i] = op[i]
                        op[i] = v[0]
                try:
                    sub = self.dp_subset(P, cents(A), refs) if tol < 0.01 else self.subset(P, A, tol, prefer=set(R))
                finally:
                    for i, v in saved.items():
                        op[i] = v
                if sub is not None and any(dvals[i] for i in sub):
                    exc = {i: dvals[i][1] for i in sub if dvals[i]}
                    amt = {i: dvals[i][2] for i in sub if dvals[i]}
                    return self.rows_for(sub, A, fx, exc, amt)
        return None


def _mitm(pos, tgt):
    """Meet-in-the-middle exact subset sum (n <= 24). Returns item ids or None."""
    h = len(pos) // 2
    L, Rr = pos[:h], pos[h:]

    def sums(part):
        out = {0: 0}
        for k, (_, v) in enumerate(part):
            for sm, mk in list(out.items()):
                ns = sm + v
                if ns <= tgt and ns not in out:
                    out[ns] = mk | (1 << k)
        return out
    a, b = sums(L), sums(Rr)
    best = None
    for sm, mk in a.items():
        m2 = b.get(tgt - sm)
        if m2 is not None:
            cnt = bin(mk).count("1") + bin(m2).count("1")
            if cnt and (best is None or cnt < best[0]):
                best = (cnt, mk, m2)
    if best is None:
        return None
    _, mk, m2 = best
    return [L[k][0] for k in range(len(L)) if mk >> k & 1] + [Rr[k][0] for k in range(len(Rr)) if m2 >> k & 1]


def _dist1(a, b):
    """True if b differs from a by one substitution or one adjacent transposition (same length)."""
    if len(a) != len(b) or a == b:
        return False
    diff = [k for k in range(len(a)) if a[k] != b[k]]
    if len(diff) == 1:
        return True
    if len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]:
        return True
    return False


def run(ctx):
    m = Matcher(ctx)
    ctx.cache["ar_matcher"] = m
    pend = ctx.pending()
    pend = pend[pend.amount > 0]
    kinds = defaultdict(int)
    for t in pend.itertuples(index=False):
        if ctx.done(t.bank_txn_id):
            continue
        r = m.run_line(t)
        if r:
            rows, kind = r
            # de-duplicate item ids
            seen, clean = set(), []
            for x in rows:
                if x["item_id"] not in seen:
                    seen.add(x["item_id"])
                    clean.append(x)
            ctx.allocate(t.bank_txn_id, clean, "ar:" + kind)
            kinds[kind] += 1
            for x in clean:
                if x["item_id"] and x["item_id"] not in ctx.open:
                    m.closed_by[x["item_id"]] = kind
    print("   ar kinds:", dict(kinds), dict(m.stats))
