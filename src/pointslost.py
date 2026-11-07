"""Points-lost table for a window (TUNE only: never run this on HOLD / GATE).

    python src/pointslost.py <submission.csv.gz> --from 2024-10-01 --to 2024-11-30 [--top 40] [--csv out.csv]

Splits the official score's loss exactly by golden segment:
  * alloc  = 50 * (FP + FN pairs of the segment) / (submitted pairs + golden pairs)    (1 - F1 = (FP+FN)/(P+G))
  * txn    = 30 * wrong lines of the segment / lines
  * the exception term (20 * mean over classes of 1 - F1_c) is shown per class, and each segment gets the number of
    pair/line exception labels it got wrong.
Wrong lines are split by kind:
  missed  = golden has items, we returned a category (usually UNIDENTIFIED) -> never matched
  wrong   = golden has items, we returned items, item set or amounts differ
  exc     = item set + amounts right, only the exception label differs
  cat     = golden has no items (category line) and we got the category/label/items wrong
"""
import argparse
import csv
import gzip
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import DEFAULT_DATA  # noqa: E402

EXC = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
       "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]


def tol(g):
    return max(0.01, 0.005 * abs(g)) + 1e-9


def load(a):
    keep = set()
    with gzip.open(os.path.join(a.data, "train", "bank_transactions.csv.gz"), "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if a.dfrom <= r["booking_date"] <= a.dto:
                keep.add(r["bank_txn_id"])
    gold = {t: [{}, "", "", "unlabelled"] for t in keep}
    with gzip.open(os.path.join(a.data, "train", "golden_allocations.csv.gz"), "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t = r["bank_txn_id"]
            if t not in keep:
                continue
            g = gold[t]
            g[3] = r.get("segment") or g[3]
            if r["item_id"]:
                g[0][r["item_id"]] = (float(r["amount"]), r["exception"])
            else:
                g[1], g[2] = r["category"], r["exception"]
    sub = defaultdict(lambda: [{}, "", ""])
    op = gzip.open if a.submission.endswith(".gz") else open
    with op(a.submission, "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            t = r["bank_txn_id"]
            if t not in keep:
                continue
            s = sub[t]
            if r["item_id"]:
                s[0][r["item_id"]] = (float(r["amount"]), r["exception"].upper())
            else:
                s[1], s[2] = r["category"].upper(), r["exception"].upper()
    return gold, sub


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("submission")
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--from", dest="dfrom", default="2024-10-01")
    ap.add_argument("--to", dest="dto", default="2024-11-30")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--csv")
    a = ap.parse_args()
    gold, sub = load(a)
    N = len(gold)
    P = sum(len(s[0]) for s in sub.values())
    G = sum(len(g[0]) for g in gold.values())
    seg = defaultdict(lambda: defaultdict(int))
    etp, efp, ecnt = defaultdict(int), defaultdict(int), defaultdict(int)
    TP = 0
    for t, (gi, gc, ge, sg) in gold.items():
        si, sc, se = sub.get(t, [{}, "", ""])
        S = seg[sg]
        S["n"] += 1
        for _, e in gi.values():
            if e:
                ecnt[e] += 1
        if not gi and ge:
            ecnt[ge] += 1
        tp = ok_rows = 0
        exc_bad = 0
        for it, (am, e) in si.items():
            g = gi.get(it)
            hit = g is not None and abs(am - g[0]) <= tol(g[0])
            if hit:
                tp += 1
                if e == g[1]:
                    ok_rows += 1
            if e:
                if g is not None and e == g[1]:
                    etp[e] += 1
                else:
                    efp[e] += 1
                    exc_bad += 1
        if not si and se:
            if not gi and se == ge:
                etp[se] += 1
            else:
                efp[se] += 1
                exc_bad += 1
        TP += tp
        S["fp"] += len(si) - tp
        S["fn"] += len(gi) - tp
        S["exc_bad"] += exc_bad
        if gi:
            ok = not (sc or se) and len(si) == len(gi) == ok_rows
            if not ok:
                if not si:
                    S["missed"] += 1
                elif tp == len(gi) == len(si):
                    S["exc"] += 1
                else:
                    S["wrong"] += 1
        else:
            ok = not si and sc == gc and se == ge
            if not ok:
                S["cat"] += 1
        S["bad"] += not ok
    f1 = 2 * TP / (P + G) if P + G else 0
    acc = 1 - sum(S["bad"] for S in seg.values()) / N
    cls = []
    for c in EXC:
        if not ecnt[c]:
            continue
        p = etp[c] / (etp[c] + efp[c]) if etp[c] + efp[c] else 0
        r = etp[c] / ecnt[c]
        cls.append((c, ecnt[c], etp[c], efp[c], 2 * p * r / (p + r) if p + r else 0, p, r))
    macro = sum(x[4] for x in cls) / len(cls) if cls else 1
    score = 100 * (0.5 * f1 + 0.3 * acc + 0.2 * macro)
    print(f"window {a.dfrom}..{a.dto}: lines={N} score={score:.2f} alloc_f1={100*f1:.2f} (P {100*TP/P if P else 0:.2f} / "
          f"R {100*TP/G if G else 0:.2f}) txn_acc={100*acc:.2f} exc_macro={100*macro:.2f}")
    print(f"lost: alloc {50*(1-f1):.2f}  txn {30*(1-acc):.2f}  exc {20*(1-macro):.2f}  (total {100-score:.2f})\n")
    print("exception classes (each worth 20/K = %.2f pts at F1 0 -> 1):" % (20 / max(1, len(cls))))
    print(f"  {'class':20s} {'gold':>6s} {'tp':>6s} {'fp':>6s}   F1     P      R    lost")
    for c, n, tp, fp, f, p, r in sorted(cls, key=lambda x: x[4]):
        print(f"  {c:20s} {n:6d} {tp:6d} {fp:6d}  {100*f:5.1f}  {100*p:5.1f}  {100*r:5.1f}  {20*(1-f)/len(cls):.3f}")
    rows = []
    for sg, S in seg.items():
        al = 50 * (S["fp"] + S["fn"]) / (P + G)
        tx = 30 * S["bad"] / N
        rows.append((al + tx, al, tx, sg, S))
    rows.sort(key=lambda x: -x[0])
    print(f"\n{'segment':34s} {'n':>6s} {'acc':>6s} {'lost':>6s} {'alloc':>6s} {'txn':>6s} {'FN':>6s} {'FP':>5s} "
          f"{'missed':>6s} {'wrong':>6s} {'exc':>5s} {'cat':>5s} {'excbad':>6s}")
    for tot, al, tx, sg, S in rows[:a.top]:
        print(f"{sg:34s} {S['n']:6d} {100*(1-S['bad']/S['n']):6.1f} {tot:6.3f} {al:6.3f} {tx:6.3f} {S['fn']:6d} "
              f"{S['fp']:5d} {S['missed']:6d} {S['wrong']:6d} {S['exc']:5d} {S['cat']:5d} {S['exc_bad']:6d}")
    k = ["missed", "wrong", "exc", "cat"]
    print("\nwrong lines by kind: " + ", ".join(f"{x}={sum(S[x] for S in seg.values())}" for x in k))
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["segment", "n", "lost", "alloc", "txn", "fn", "fp"] + k + ["exc_bad"])
            for tot, al, tx, sg, S in rows:
                w.writerow([sg, S["n"], f"{tot:.4f}", f"{al:.4f}", f"{tx:.4f}", S["fn"], S["fp"]] + [S[x] for x in k] + [S["exc_bad"]])


if __name__ == "__main__":
    main()
