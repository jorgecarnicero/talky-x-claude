"""Derive a submission where some pass claims are dropped: those lines get the team-v3 answer, exactly what the last
pass (v3rest, which ignores the ledger) would have given them. Used to evaluate a stricter acceptance rule offline.

    python tools/drop_claims.py SUB SOURCES V3_SUB OUT --prefix ar_payer:top:subset_dp_prim_m ar_payer:top:subset_dp_prim_l
    python tools/drop_claims.py SUB SOURCES V3_SUB OUT --prefix ar_payer        # = pipeline without ar_payer
"""
import argparse
import csv
import gzip


def rows(p):
    op = gzip.open if p.endswith(".gz") else open
    with op(p, "rt", encoding="utf-8", newline="") as fh:
        yield from csv.DictReader(fh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sub")
    ap.add_argument("sources")
    ap.add_argument("v3")
    ap.add_argument("out")
    ap.add_argument("--prefix", nargs="+", required=True)
    a = ap.parse_args()
    drop = {r["bank_txn_id"] for r in rows(a.sources) if any(r["source"].startswith(p) for p in a.prefix)}
    v3 = {}
    for r in rows(a.v3):
        v3.setdefault(r["bank_txn_id"], []).append(r)
    cols = ["bank_txn_id", "item_id", "amount", "category", "exception"]
    n = 0
    with gzip.open(a.out, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, cols, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        done = set()
        for r in rows(a.sub):
            t = r["bank_txn_id"]
            if t in drop:
                if t not in done:
                    done.add(t)
                    n += 1
                    for x in v3.get(t, []):
                        w.writerow(x)
                continue
            w.writerow(r)
    print(f"dropped {n} claims -> {a.out}")


if __name__ == "__main__":
    main()
