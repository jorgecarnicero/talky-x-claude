"""Paired bootstrap of the official score difference between two submissions on one window.

    python src/compare.py <base.csv.gz> <new.csv.gz> --from 2024-10-01 --to 2024-11-30 [--b 2000] [--cluster payer|day|line]

The score is a ratio of sums over bank lines, so each line contributes a vector (tp pairs, submitted pairs, golden
pairs, line correct, and per exception class tp / fp / gold). Resampling lines (or whole booking days, to respect
correlation inside a day/batch) with replacement and recomputing both scores on the SAME resample gives the
distribution of the delta. Reports delta, 95 % interval and P(delta <= 0).
Prints totals only, so it is safe to use on HOLD.
"""
import argparse
import csv
import gzip
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import DEFAULT_DATA  # noqa: E402
from pointslost import EXC, load, tol  # noqa: E402

K = len(EXC)


def vectors(gold, sub, order):
    """per line: [tp, P, G, ok, tp_c..., fp_c..., gold_c...]"""
    ci = {c: i for i, c in enumerate(EXC)}
    M = np.zeros((len(order), 4 + 3 * K))
    for r, t in enumerate(order):
        gi, gc, ge, _ = gold[t]
        si, sc, se = sub.get(t, [{}, "", ""])
        v = M[r]
        for _, e in gi.values():
            if e in ci:
                v[4 + 2 * K + ci[e]] += 1
        if not gi and ge in ci:
            v[4 + 2 * K + ci[ge]] += 1
        tp = okr = 0
        for it, (am, e) in si.items():
            g = gi.get(it)
            if g is not None and abs(am - g[0]) <= tol(g[0]):
                tp += 1
                okr += e == g[1]
            if e in ci:
                v[4 + (0 if g is not None and e == g[1] else K) + ci[e]] += 1
        if not si and se in ci:
            v[4 + (0 if not gi and se == ge else K) + ci[se]] += 1
        v[0], v[1], v[2] = tp, len(si), len(gi)
        v[3] = (not (sc or se) and len(si) == len(gi) == okr) if gi else (not si and sc == gc and se == ge)
    return M


def score(S, n):
    """S: (..., cols) column sums; n: lines per resample"""
    f1 = 2 * S[..., 0] / np.maximum(S[..., 1] + S[..., 2], 1)
    acc = S[..., 3] / n
    tp, fp, g = S[..., 4:4 + K], S[..., 4 + K:4 + 2 * K], S[..., 4 + 2 * K:]
    f1c = 2 * tp / np.maximum(tp + fp + g, 1e-9)
    present = g > 0
    macro = (f1c * present).sum(-1) / np.maximum(present.sum(-1), 1)
    return 100 * (0.5 * f1 + 0.3 * acc + 0.2 * macro)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("new")
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--from", dest="dfrom", required=True)
    ap.add_argument("--to", dest="dto", required=True)
    ap.add_argument("--b", type=int, default=2000)
    ap.add_argument("--cluster", default="payer", choices=["payer", "day", "line"])
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    a.submission = a.base
    gold, sb = load(a)
    a.submission = a.new
    _, sn = load(a)
    order = sorted(gold)
    A, B = vectors(gold, sb, order), vectors(gold, sn, order)
    if a.cluster in ("day", "payer"):
        # payer = counterparty account, else entity + counterparty name, else the line itself (lines of one payer are
        # correlated: same habits, same open items)
        day = {}
        with gzip.open(os.path.join(a.data, "train", "bank_transactions.csv.gz"), "rt", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["bank_txn_id"] in gold:
                    if a.cluster == "day":
                        day[r["bank_txn_id"]] = r["booking_date"]
                    else:
                        nm = " ".join(r["counterparty_name"].upper().split())
                        day[r["bank_txn_id"]] = (r["counterparty_account"] or (r["entity"] + "|" + nm if nm else "")
                                                 or r["bank_txn_id"])
        keys = sorted(set(day.values()))
        kid = {k: i for i, k in enumerate(keys)}
        grp = np.array([kid[day[t]] for t in order])
    else:
        grp = np.arange(len(order))
    ng = grp.max() + 1
    GA = np.zeros((ng, A.shape[1])); np.add.at(GA, grp, A)
    GB = np.zeros((ng, B.shape[1])); np.add.at(GB, grp, B)
    cnt = np.bincount(grp, minlength=ng).astype(float)
    base_s, new_s = score(GA.sum(0), len(order)), score(GB.sum(0), len(order))
    rng = np.random.default_rng(a.seed)
    W = rng.multinomial(ng, np.full(ng, 1 / ng), size=a.b).astype(float)  # (b, ng) resample counts
    n = W @ cnt
    d = score(W @ GB, n) - score(W @ GA, n)
    lo, hi = np.percentile(d, [2.5, 97.5])
    changed = int((np.abs(A - B).sum(1) > 0).sum())
    lo1 = np.percentile(d, 5)
    print(f"base {base_s:.3f}  new {new_s:.3f}  delta {new_s - base_s:+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]  "
          f"one-sided-95% lower {lo1:+.3f}  se {d.std():.3f}  P(delta<=0)={float((d <= 0).mean()):.3f}  "
          f"lines changed={changed}  clusters={ng}  ({a.cluster}-bootstrap, b={a.b})")


if __name__ == "__main__":
    main()
