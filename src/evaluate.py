"""Score a TRAIN submission with the OFFICIAL scorer, restricted to a booking-date window.

    python3 -I src/evaluate.py out/sub_train.csv.gz --from 2025-01-01 --to 2025-06-30

Default window = HOLDOUT (2025-01-01..2025-06-30): never tune rules on it casually.
The submission may cover more lines than the window; rows outside it are dropped before scoring.
"""
import argparse, csv, gzip, io, json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import DEFAULT_DATA  # noqa: E402

EXC = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
       "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("submission")
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--phase", default="train", choices=["train", "sample"])
    ap.add_argument("--from", dest="dfrom", default="2025-01-01")
    ap.add_argument("--to", dest="dto", default="2025-06-30")
    ap.add_argument("--segments", type=int, default=45)
    a = ap.parse_args()
    sys.path.insert(0, os.path.join(a.data, "starter"))
    import bankrec_score as S

    folder = os.path.join(a.data, a.phase)
    cats = [r["category"] for r in csv.DictReader(open(os.path.join(a.data, "masters", "categories.csv"), encoding="utf-8"))]
    keep = set()
    with gzip.open(os.path.join(folder, "bank_transactions.csv.gz"), "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if a.dfrom <= r["booking_date"] <= a.dto:
                keep.add(r["bank_txn_id"])
    tmpd = tempfile.mkdtemp()
    tx, gd = os.path.join(tmpd, "t.csv.gz"), os.path.join(tmpd, "g.csv.gz")
    with gzip.open(tx, "wt") as out:
        out.write("bank_txn_id\n" + "".join(t + "\n" for t in sorted(keep)))
    with gzip.open(os.path.join(folder, "golden_allocations.csv.gz"), "rt", encoding="utf-8", newline="") as fh, \
            gzip.open(gd, "wt", newline="") as out:
        rd, w = csv.reader(fh), csv.writer(out, lineterminator="\n")
        w.writerow(next(rd))
        for row in rd:
            if row[0] in keep:
                w.writerow(row)
    g = S.load_golden(gd, tx, EXC, cats)
    op = gzip.open if a.submission.endswith(".gz") else open
    buf = io.StringIO()
    with op(a.submission, "rt", encoding="utf-8", newline="") as fh:
        rd, w = csv.reader(fh), csv.writer(buf, lineterminator="\n")
        h = next(rd); w.writerow(h); i = h.index("bank_txn_id")
        for row in rd:
            if row and row[i] in keep:
                w.writerow(row)
    res = S.score_submission(g, buf.getvalue().encode(), "sub.csv")
    print(f"window {a.dfrom}..{a.dto}: {len(keep)} bank lines")
    print(json.dumps({k: res[k] for k in ("ok", "score", "metrics")}, indent=1))
    if res["errors"]:
        print("ERRORS:", res["errors"][:20])
    for k, v in sorted(res["segments"].items(), key=lambda x: -x[1]["n"])[:a.segments]:
        print(f"  {k:32s} n={v['n']:7d} acc={v['accuracy']:.3f}")


if __name__ == "__main__":
    main()
