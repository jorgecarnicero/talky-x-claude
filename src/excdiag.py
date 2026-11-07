"""Exception-label diagnostics on a window (TUNE only: never run on HOLD / GATE).

    python src/excdiag.py <sub.csv.gz> --sources <sub.src.csv> --from 2024-10-01 --to 2024-11-30

1. Confusion of exception labels on item rows whose item is in the golden line (gold label x our label), per item
   type (ARI / ARC / API / APC).
2. Per (source pass x our label): rows, precision (label == golden label on a golden item). Labels whose precision is
   below F1_c / 2 lose macro-F1 (Lipton et al. 2014, docs/research/exceptions.md).
3. Golden labels we never emit on the right row: recall misses per class and item type.
"""
import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import DEFAULT_DATA  # noqa: E402
from pointslost import load  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("submission")
    ap.add_argument("--sources")
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--from", dest="dfrom", default="2024-10-01")
    ap.add_argument("--to", dest="dto", default="2024-11-30")
    ap.add_argument("--min", type=int, default=5)
    a = ap.parse_args()
    gold, sub = load(a)
    src = {}
    if a.sources:
        with open(a.sources, encoding="utf-8") as fh:
            for r in csv.reader(fh):
                if len(r) >= 2:
                    src[r[0]] = r[1]
    conf = defaultdict(Counter)        # item type -> (gold, ours) -> n
    by_src = defaultdict(Counter)      # (source, our label) -> ok / n
    miss = Counter()                   # (gold label, item type) -> n golden rows not labelled right
    for t, (gi, gc, ge, _) in gold.items():
        si, sc, se = sub.get(t, [{}, "", ""])
        s = src.get(t, "?")
        for it, (am, e) in si.items():
            g = gi.get(it)
            if e:
                by_src[(s, e)]["n"] += 1
                by_src[(s, e)]["ok"] += g is not None and g[1] == e
            if g is not None:
                conf[it[:3]][(g[1] or "-", e or "-")] += 1
        if not si and se:
            by_src[(s, se)]["n"] += 1
            by_src[(s, se)]["ok"] += (not gi) and ge == se
        for it, (_, e) in gi.items():
            if e and (it not in si or si[it][1] != e):
                miss[(e, it[:3])] += 1
        if not gi and ge and not (not si and se == ge):
            miss[(ge, "line")] += 1
    print("== confusion on golden items (gold -> ours), off-diagonal with n >= %d" % a.min)
    for ty in sorted(conf):
        for (g, o), n in conf[ty].most_common():
            if g != o and n >= a.min:
                print(f"  {ty}  {g:20s} -> {o:20s} {n:6d}")
    print("\n== precision per (source pass, our label)")
    for (s, e), c in sorted(by_src.items(), key=lambda x: (x[0][1], -x[1]["n"])):
        if c["n"] >= a.min:
            print(f"  {e:20s} {s:16s} n={c['n']:6d}  prec={c['ok'] / c['n']:.3f}")
    print("\n== golden labels missed (class, item type)")
    for (e, ty), n in miss.most_common(30):
        print(f"  {e:20s} {ty:5s} {n:6d}")


if __name__ == "__main__":
    main()
