#!/usr/bin/env python3
"""Rewrite receipts answered UNIDENTIFIED with a best-guess invoice subset of an identified payer.
usage: postfix.py SUB.csv.gz --phase train|live|final [--from D --to D] --out NEW.csv.gz [--tau 0.6] [--maxk 3]
Only rows with empty item and category UNIDENTIFIED (amount > 0) are touched; items already in the file are never reused."""
import argparse, csv, gzip, os, re, sys, unicodedata, itertools, json
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from data import DEFAULT_DATA  # noqa: E402
ROOT = DEFAULT_DATA

def norm(s):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().upper()
    s = re.sub(r'[^A-Z0-9 ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()
SUF = re.compile(r'\b(S ?L|S ?A|SAS|SARL|SRL|SPA|GMBH|AG|LTD|LIMITED|PLC|INC|LLC|BV|NV|EURL|SNC|PTY|CO|KG|OHG|SL|SA|LDA|CORP|SAU|SLU|PTE|BHD|KK|AB|AS|OY|SP Z O O|DE CV|LLP|PVT)\b')
def norm2(s):
    return re.sub(r'\s+', ' ', SUF.sub(' ', norm(s))).strip()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sub'); ap.add_argument('--phase', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--from', dest='d0'); ap.add_argument('--to', dest='d1')
    ap.add_argument('--tau', type=float, default=0.6); ap.add_argument('--maxk', type=int, default=3)
    ap.add_argument('--log'); ap.add_argument('--mode', default='fill'); ap.add_argument('--skip', type=int, default=0); ap.add_argument('--maxgap', type=float, default=1.0); ap.add_argument('--win', type=int, default=100); ap.add_argument('--hidue', type=int, default=0); ap.add_argument('--no_iban', action='store_true'); ap.add_argument('--digits', type=int, default=1)
    a = ap.parse_args()
    if a.phase == 'train':
        F = os.path.join(HERE, 'train_win'); M = os.path.join(ROOT, 'masters')
    elif a.phase == 'live':
        F = os.path.join(ROOT, 'live'); M = os.path.join(ROOT, 'masters')
    else:
        F = os.path.join(ROOT, 'final'); M = os.path.join(F, 'masters_final')
        if not os.path.isdir(M): M = os.path.join(ROOT, 'masters')
    sub = pd.read_csv(a.sub, dtype=str, keep_default_na=False)
    U = sub[(sub.item_id == '') & (sub.category == 'UNIDENTIFIED')]
    uset = set(U.bank_txn_id)
    used = set(sub.item_id[sub.item_id != ''])
    bk = pd.read_csv(os.path.join(F, 'bank_transactions.csv.gz'), dtype=str, keep_default_na=False,
                     usecols=['bank_txn_id', 'entity', 'booking_date', 'amount', 'currency', 'counterparty_name', 'counterparty_account', 'text', 'remittance_info', 'ref1', 'ref2', 'end_to_end_id'])
    bk = bk[bk.bank_txn_id.isin(uset)]
    bk = bk[bk.amount.astype(float) > 0].sort_values(['booking_date', 'bank_txn_id'])
    cu = pd.read_csv(os.path.join(M, 'customers.csv'), dtype=str, keep_default_na=False)
    names = {}; ibans = {}
    def addn(ent, nm, cid):
        for k in {norm(nm), norm2(nm)}:
            if len(k) >= 4: names.setdefault((ent, k), set()).add(cid)
    for r in cu.itertuples():
        addn(r.entity, r.name, r.customer_id)
        if r.trade_name: addn(r.entity, r.trade_name, r.customer_id)
        if r.iban: ibans.setdefault(r.iban.replace(' ', ''), set()).add(r.customer_id)
    ent_names = {}
    for (e, k), cs in list(names.items()):
        if len(k) >= 10 and len(cs) == 1: ent_names.setdefault(e, []).append((k, next(iter(cs))))
    # items: opening balances + items issued in phase
    oi = pd.read_csv(os.path.join(F, 'open_items_at_start.csv.gz'), dtype=str, keep_default_na=False)
    oi = oi[oi.item_type == 'ARI'][['item_id', 'entity', 'party_id', 'document_number', 'issue_date', 'due_date', 'currency', 'open_amount']]
    inv = pd.read_csv(os.path.join(F, 'ar_invoices.csv.gz'), dtype=str, keep_default_na=False,
                      usecols=['item_id', 'entity', 'customer_id', 'customer_name', 'document_number', 'issue_date', 'due_date', 'currency', 'total_amount'])
    for r in inv[['entity', 'customer_name', 'customer_id']].drop_duplicates().itertuples():
        addn(r.entity, r.customer_name, r.customer_id)
    ent_names = {}
    for (e, k), cs in list(names.items()):
        if len(k) >= 10 and len(cs) == 1: ent_names.setdefault(e, []).append((k, next(iter(cs))))
    if a.phase == 'train':
        inv = inv[(inv.issue_date >= a.d0)]
    inv = inv[~inv.item_id.isin(set(oi.item_id))].rename(columns={'customer_id': 'party_id', 'total_amount': 'open_amount'}).drop(columns=['customer_name'])
    items = pd.concat([oi, inv])
    items = items[~items.item_id.isin(used)]
    items['cents'] = (items.open_amount.astype(float) * 100).round().astype('int64')
    pools = {}
    for r in items.sort_values(['due_date', 'issue_date', 'item_id']).itertuples(index=False):
        pools.setdefault(r.party_id, []).append((r.item_id, r.issue_date, r.due_date, r.currency, r.cents, r.document_number))
    taken = set(); new = {}; log = []
    for r in bk.itertuples(index=False):
        ent = r.entity; amt = int(round(float(r.amount) * 100))
        cands = set()
        for k in {norm(r.counterparty_name), norm2(r.counterparty_name)}:
            cands |= names.get((ent, k), set())
        if not cands and not a.no_iban and r.counterparty_account:
            cands |= ibans.get(r.counterparty_account.replace(' ', ''), set())
        if not cands:
            tn = ' ' + norm(' '.join([r.text, r.remittance_info, r.counterparty_name])) + ' '
            hits = {cid for k, cid in ent_names.get(ent, []) if (' ' + k + ' ') in tn}
            if len(hits) == 1: cands = hits
        if not cands or len(cands) > 3: continue
        bd = pd.Timestamp(r.booking_date)
        lo_issue = (bd - pd.Timedelta(days=a.win)).strftime('%Y-%m-%d'); hi_due = (bd + pd.Timedelta(days=a.hidue)).strftime('%Y-%m-%d')
        pool = []
        for c in cands:
            for it in pools.get(c, []):
                if it[0] in taken or it[1] > r.booking_date or it[3] != r.currency or it[4] <= 0: continue
                if a.win and (it[1] < lo_issue or it[2] > hi_due): continue
                pool.append(it)
        if not pool: continue
        pool.sort(key=lambda x: (x[2], x[1], x[0]))
        txt = ' '.join([r.text, r.remittance_info, r.ref1, r.ref2, r.end_to_end_id]).upper()
        # text hints: document numbers / their digit tails
        hint = {it[0] for it in pool if it[5] and (it[5].upper() in txt or (len(it[5]) > 6 and it[5][-6:] in txt))}
        if a.digits:
            toks = [t for t in re.findall(r'\d{6,}', txt)]
            dd = [(it[0], re.sub(r'\D', '', it[5])) for it in pool]
            for t in toks:
                m = [i for i, d in dd if t in d]
                if 1 <= len(m) <= 2: hint.update(m)
        if a.mode == 'fill':
            pr = sorted(pool, key=lambda x: (0 if x[0] in hint else 1, x[2], x[1], x[0]))
            pick = []; s = 0
            for it in pr:
                if s + it[4] <= amt + 1:
                    pick.append(it); s += it[4]
                elif not a.skip: break
            if not pick: continue
            gap = (amt - s) / max(amt, 1)
            ids = {x[0] for x in pick}
            log.append(dict(bank_txn_id=r.bank_txn_id, kind='fill', k=len(pick), uniq=0, p=1 - abs(gap), ncand=len(cands), pool=len(pool), gap=gap, items='|'.join(sorted(ids))))
            if abs(gap) <= a.maxgap:
                taken |= ids; new[r.bank_txn_id] = [(x[0], '%.2f' % (x[4] / 100)) for x in pick]
            continue
        sols = []  # (kind, items)
        # oldest-due prefix
        s = 0
        for i, it in enumerate(pool[:40]):
            s += it[4]
            if s == amt: sols.append(('prefix', tuple(pool[:i + 1]))); break
            if s > amt: break
        P = pool[:40]
        found_k = None
        for k in range(1, a.maxk + 1):
            if found_k is not None: break
            if k == 1:
                kk = [(x,) for x in P if x[4] == amt]
            elif k == 2:
                byc = {}
                kk = []
                for j, x in enumerate(P):
                    for i in byc.get(amt - x[4], []): kk.append((P[i], x))
                    byc.setdefault(x[4], []).append(j)
            else:
                if len(P) > 30: P = P[:30]
                kk = [c for c in itertools.combinations(P, k) if sum(x[4] for x in c) == amt]
            if kk: found_k = k; sols += [('k%d' % k, c) for c in kk]
        if not sols: continue
        n_min = sum(1 for kind, c in sols if kind != 'prefix')
        pos = {it[0]: i for i, it in enumerate(pool)}
        def rank(sc):
            kind, c = sc
            return (0 if kind == 'prefix' else 1, -len(hint & {x[0] for x in c}), len(c), sum(pos[x[0]] for x in c))
        sols.sort(key=rank)
        kind, best = sols[0]
        ids = {x[0] for x in best}
        uniq = len({frozenset(x[0] for x in c) for _, c in sols})
        if hint & ids: p = 0.9
        elif kind == 'prefix': p = 0.85 if uniq == 1 else 0.7
        else: p = (0.75 if uniq == 1 else 0.6 / uniq) * (1.0 if len(cands) == 1 else 0.7)
        log.append(dict(bank_txn_id=r.bank_txn_id, kind=kind, k=len(best), uniq=uniq, p=p, ncand=len(cands), pool=len(pool), items='|'.join(sorted(ids))))
        if p >= a.tau:
            taken |= ids
            new[r.bank_txn_id] = [(x[0], '%.2f' % (x[4] / 100)) for x in best]
    rows = []
    for row in sub.itertuples(index=False):
        if row.bank_txn_id in new:
            if row.item_id == '':
                for it, am in new[row.bank_txn_id]: rows.append((row.bank_txn_id, it, am, '', ''))
            continue
        rows.append(tuple(row))
    with gzip.open(a.out, 'wt', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n'); w.writerow(['bank_txn_id', 'item_id', 'amount', 'category', 'exception']); w.writerows(rows)
    if a.log: pd.DataFrame(log).to_csv(a.log, index=False)
    print(f'UNIDENTIFIED receipts {len(bk)}; guessed {len(log)}; rewritten {len(new)} lines at tau={a.tau}', file=sys.stderr)

if __name__ == '__main__':
    main()
