"""Payer identification for AR receipts whose payer the base matchers could not establish (model/v5-matching).

Evidence combined per candidate customer (no IDs / names / IBANs / dates / segments hard-coded; no `segment` field):
  * name variants scored ONE BY ONE (not a bag of every token): master name, trade name, names printed on documents
    issued on or before the bank line, and payer names seen on earlier bank lines;
  * token IDF over customers, word-prefix matching for truncated texts ("CONSTR MARTINEZ IND"), in-order truncation
    ("OUTDOOR LIVING HUGHE" for "Outdoor Living Hughes ..."), legal form agreement / conflict ("... LLC" vs "... CO");
  * IBAN evidence: master IBAN, and IBAN -> customer counts from earlier labelled bank lines (diluted when shared);
  * exact payer-string aliases from earlier labelled bank lines (counterparty name, or leading words of the text).

History = TRAIN golden of bank lines booked strictly BEFORE the first bank line of the phase being reconciled
(dev dataset: train/ is already cut at the window start; LIVE/FINAL: all of TRAIN). Golden labels of the evaluated
period are never read.
"""
import math
import os
import re
import unicodedata
from collections import Counter, defaultdict

import pandas as pd

LEGAL = set("""SL SA SAS SARL EURL SLU SAU SCOOP COOP GMBH KG EK AG LTD LDA SNC SRL SRLS SPA BV NV INC LLC PLC SP ZOO
OO Z CORP CO PTY LIMITED CORPORATION COMPANY OHG UG MBH SCA SCS SASU DE CV LTDA EIRELI ME PC LP LLP AB AS OY KFT SRO
SPZOO SARLAU SAPI SE SPRL BVBA VOF ULC""".split())
CONNECT = set("DE DEL LA LAS LOS EL Y E ET DU DES LE LES UND THE AND OF FOR A EN AU DA DI DO DOS DAS VAN DER DEN I".split())
ABBREV = {"HNOS": "HERMANOS", "CIA": "COMPANIA", "STE": "SOCIETE", "INTL": "INTERNATIONAL", "GPO": "GRUPO",
          "SDAD": "SOCIEDAD", "SOC": "SOCIEDAD", "SVCS": "SERVICES", "SVC": "SERVICE", "MGMT": "MANAGEMENT",
          "BROS": "BROTHERS", "ASSOC": "ASSOCIATES"}
_SPLIT = re.compile(r"[^A-Z0-9]+")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{12,30}\b")


# ----------------------------------------------------------------------------- normalisation
def strip_acc(s):
    return unicodedata.normalize("NFKD", s if isinstance(s, str) else "").encode("ascii", "ignore").decode()


def norm_tokens(s):
    """ordered tokens: accents removed, upper case, dotted abbreviations joined ("S.p.A." -> SPA), abbreviations
    expanded"""
    s = strip_acc(s).upper().replace(".", "")
    return [ABBREV.get(t, t) for t in _SPLIT.split(s) if t]


def split_name(s):
    """name -> (core tokens, legal-form tokens). Core drops connectors, digits and 1-letter tokens."""
    toks = norm_tokens(s)
    suf = []
    while len(toks) > 1 and toks[-1] in LEGAL:
        suf.insert(0, toks.pop())
    core = [t for t in toks if len(t) >= 2 and t not in CONNECT and not t.isdigit()]
    return tuple(dict.fromkeys(core)), tuple(suf)


def _s(v):
    return v if isinstance(v, str) else ""


def alias_key(cp, text):
    """exact payer string of a bank line: counterparty name when given, else the leading words of the text"""
    if _s(cp):
        core, suf = split_name(cp)
        return "N:" + " ".join(core + suf) if core else None
    toks = [x for x in norm_tokens(text) if not any(ch.isdigit() for ch in x)]
    return "T:" + " ".join(toks[:6]) if toks else None


# ----------------------------------------------------------------------------- history (strictly before the phase)
def build_history(ctx):
    """IBAN -> customer and payer-string -> customer counts from TRAIN golden lines booked before the phase start."""
    from data import _cached, _read, load_bank, load_items
    root = ctx.root
    gpath = os.path.join(root, "train", "golden_allocations.csv.gz")
    if not os.path.exists(gpath) or not len(ctx.bank):
        return {}
    start = ctx.bank.booking_date.min()
    g = _cached("golden_train", lambda: _read(gpath))
    g = g[g.item_id.str.startswith("AR")]
    it = load_items(root, "train")
    party = dict(zip(it.item_id, it.party_id))
    g = g.assign(party=g.item_id.map(party)).dropna(subset=["party"])
    np_ = g.groupby("bank_txn_id").party.nunique()
    one = g[g.bank_txn_id.isin(np_[np_ == 1].index)].drop_duplicates("bank_txn_id")
    b = load_bank(root, "train")
    b = b[b.booking_date < start]  # never read lines of the period being reconciled
    b = b.merge(one[["bank_txn_id", "party"]], on="bank_txn_id")
    iban = defaultdict(Counter)
    akey = defaultdict(Counter)
    avar = {}
    for ent, cp, acc, txt, bd, p in zip(b.entity, b.counterparty_name, b.counterparty_account, b.text,
                                        b.booking_date, b.party):
        if _s(acc):
            iban[acc][p] += 1
        k = alias_key(cp, txt)
        if k:
            akey[(ent, k)][p] += 1
        if _s(cp):
            core, _ = split_name(cp)
            if core:
                c, dt = avar.get((p, core), (0, bd))
                avar[(p, core)] = (c + 1, min(dt, bd))
    return {"iban": dict(iban), "alias_key": dict(akey), "alias_variants": avar, "start": start}


# ----------------------------------------------------------------------------- index
class PayerIndex:
    KEEP = 150  # full variant scoring only on the best pre-ranked candidates

    def __init__(self, ctx, history):
        cu = ctx.masters["customers"]
        self.variants = defaultdict(list)  # cust -> [(core, suffix, known_from 'YYYY-MM-DD' | '', source)]
        self.entity = dict(zip(cu.customer_id, cu.entity))
        for cid, nm, tn in zip(cu.customer_id, cu.name, cu.trade_name):
            for v, src in ((nm, "master"), (tn, "trade")):
                if _s(v):
                    core, suf = split_name(v)
                    if core:
                        self.variants[cid].append((core, suf, "", src))
        it = ctx.items
        ar = it[it.item_type.astype(str).isin(["ARI", "ARC"]) & (it.party_name != "")]
        first = ar.groupby(["party_id", "party_name"]).issue_date.min()
        for (cid, nm), dt in first.items():
            core, suf = split_name(nm)
            if core:
                self.variants[cid].append((core, suf, dt, "doc"))
        for (cid, core), (cnt, dt) in history.get("alias_variants", {}).items():
            self.variants[cid].append((core, (), dt, "bank"))
        for cid, vs in self.variants.items():  # dedupe: earliest date per (core, suffix)
            best = {}
            for core, suf, dt, src in vs:
                k = (core, suf)
                if k not in best or (best[k][2] and (not dt or dt < best[k][2])):
                    best[k] = (core, suf, dt, src)
            self.variants[cid] = list(best.values())
        df = Counter()
        self.tok_idx = defaultdict(lambda: defaultdict(set))
        self.ptoks = {}
        for cid, vs in self.variants.items():
            toks = set()
            for core, _, _, _ in vs:
                toks.update(core)
            self.ptoks[cid] = toks
            ent = self.entity.get(cid)
            for t in toks:
                df[t] += 1
                self.tok_idx[ent][t].add(cid)
        n = max(1, len(self.variants))
        self.idf = {t: math.log(n / c) for t, c in df.items()}
        self.ptot = {c: (sum(self.idf.get(x, 0) for x in tk) or 1.0) for c, tk in self.ptoks.items()}
        self.prefix = defaultdict(set)
        for t in df:
            for k in range(3, len(t)):
                self.prefix[t[:k]].add(t)
        self.iban_hist = history.get("iban", {})
        self.alias_hist = history.get("alias_key", {})
        self.master_iban = defaultdict(set)
        for cid, ib in zip(cu.customer_id, cu.iban):
            if _s(ib):
                self.master_iban[ib].add(cid)

    @staticmethod
    def line_seq(t):
        seq = []
        for v in (t.counterparty_name, t.text, t.ref2, t.remittance_info):
            if _s(v):
                seq += [x for x in norm_tokens(v) if not any(ch.isdigit() for ch in x)] + ["|"]
        return seq

    def variant_score(self, core, suf, bset, seq, seq_str):
        idf = self.idf
        tot = hit = 0.0
        for vt in core:
            w = idf.get(vt, 1.0)
            tot += w
            if vt in bset:
                hit += w
            elif len(vt) >= 4 and any(len(b) >= 3 and b[0] == vt[0] and vt.startswith(b) for b in bset):
                hit += 0.75 * w  # truncated word
        if tot <= 0 or hit <= 0:
            return 0.0
        cov = hit / tot
        # in-order truncation: the first k name words appear in sequence and the text stops / the next word is cut
        try:
            i = seq.index(core[0])
        except ValueError:
            i = -1
        if i >= 0:
            k = 0
            while k < len(core) and i + k < len(seq) and seq[i + k] == core[k]:
                k += 1
            if k < len(core) and i + k < len(seq) and len(seq[i + k]) >= 2 and seq[i + k] != core[k] \
                    and core[k].startswith(seq[i + k]):
                k += 1
            seen = sum(idf.get(x, 1.0) for x in core[:k])
            if 2 <= k < len(core) and seen >= 5.0:
                cov = max(cov, 0.55 + 0.4 * seen / tot)
        s = cov
        name = " " + " ".join(core) + " "
        if suf and " " + " ".join(core + suf) + " " in seq_str:
            s += 0.35  # full legal name incl. legal form
        elif name in seq_str:
            s += 0.15
            if suf:  # a different legal form right after the name: lookalike payer
                j = seq_str.find(name) + len(name)
                nxt = seq_str[j:].split(" ", 1)[0]
                if nxt in LEGAL and nxt not in suf:
                    s -= 0.4
        return s

    def candidates(self, t, max_pool=3000):
        """[(customer, score, evidence)] best first, customers of the line's entity"""
        ent, bday = t.entity, t.booking_date
        seq = self.line_seq(t)
        bset = {x for x in seq if x != "|" and len(x) >= 2}
        seq_str = " " + " ".join(seq) + " "
        idx = self.tok_idx.get(ent, {})
        gen = []
        for b in bset:
            if b in idx:
                gen.append((len(idx[b]), b))
            elif len(b) >= 3:
                for full in self.prefix.get(b, ()):
                    if full in idx:
                        gen.append((len(idx[full]), full))
        gen.sort()
        pool = set()
        for n_, tk in gen[:6]:
            if n_ <= 2000 and len(pool) + n_ <= max_pool:
                pool |= idx[tk]
        ibans = [t.counterparty_account] if _s(t.counterparty_account) else []
        ibans += _IBAN.findall(_s(t.text))
        ev_iban = {}
        for x in ibans:
            for p in self.master_iban.get(x, ()):
                ev_iban[p] = max(ev_iban.get(p, 0), 1.0 / len(self.master_iban[x]))
            h = self.iban_hist.get(x)
            if h:
                tot = sum(h.values())
                for p, c in h.items():
                    ev_iban[p] = max(ev_iban.get(p, 0), c / tot)
        ak = alias_key(t.counterparty_name, t.text)
        ev_alias = {}
        h = self.alias_hist.get((ent, ak)) if ak else None
        if h:
            tot = sum(h.values())
            ev_alias = {p: c / tot for p, c in h.items()}
        pool |= set(ev_iban) | set(ev_alias)
        expanded = set(bset)
        for b in bset:
            px = self.prefix.get(b, set())
            if (len(b) >= 4 or b not in self.idf) and len(px) <= 50:
                expanded |= px
        quick = []
        for p in pool:
            if self.entity.get(p) != ent:
                continue
            tk = self.ptoks.get(p)
            m = sum(self.idf.get(x, 0) for x in tk & expanded) if tk else 0.0
            quick.append((-(m / self.ptot.get(p, 1.0)) - ev_iban.get(p, 0) - ev_alias.get(p, 0), p))
        quick.sort()
        out = []
        for _, p in quick[:self.KEEP]:
            best = 0.0
            for core, suf, dt, src in self.variants.get(p, ()):
                if dt and dt > bday:
                    continue  # variant not known yet at the booking date
                s = self.variant_score(core, suf, bset, seq, seq_str)
                if s > best:
                    best = s
            sc = best + ev_iban.get(p, 0) + 0.5 * ev_alias.get(p, 0)
            if sc > 0.3:
                out.append((p, sc, {"name": round(best, 3), "iban": round(ev_iban.get(p, 0), 3),
                                    "alias": round(ev_alias.get(p, 0), 3)}))
        out.sort(key=lambda x: (-x[1], x[0]))
        return out
