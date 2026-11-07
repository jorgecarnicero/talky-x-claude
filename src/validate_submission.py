"""Check a submission against every FORMAT.md validation rule BEFORE uploading (esp. the one-shot FINAL).

    .venv/bin/python src/validate_submission.py ../out/live.csv.gz --phase live
"""
import argparse, csv, gzip, os, re, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import DEFAULT_DATA, EXCEPTIONS  # noqa: E402

ITEM_RE = re.compile(r"^(ARI|ARC|API|APC)-\d{4}-\d{7}$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("submission")
    ap.add_argument("--phase", required=True, choices=["train", "live", "final", "sample"])
    ap.add_argument("--data", default=DEFAULT_DATA)
    a = ap.parse_args()
    errs = []
    size = os.path.getsize(a.submission)
    op = gzip.open if a.submission.endswith(".gz") else open
    raw = op(a.submission, "rb").read()
    if len(raw) > 60e6:
        errs.append(f"uncompressed size {len(raw)/1e6:.1f} MB > 60 MB")
    # masters/ overlaid file by file with final/masters_final/ (it may contain only the files that changed)
    cf = os.path.join(a.data, "final", "masters_final", "categories.csv")
    if a.phase != "final" or not os.path.exists(cf):
        cf = os.path.join(a.data, "masters", "categories.csv")
    cats = {r["category"] for r in csv.DictReader(open(cf, encoding="utf-8"))}
    with gzip.open(os.path.join(a.data, a.phase, "bank_transactions.csv.gz"), "rt", encoding="utf-8") as fh:
        phase_ids = {r["bank_txn_id"] for r in csv.DictReader(fh)}
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    hdr = [h.strip() for h in rows[0]]
    if sorted(hdr) != sorted(["bank_txn_id", "item_id", "amount", "category", "exception"]):
        errs.append(f"bad header {hdr}")
        print("\n".join(errs)); sys.exit(1)
    ix = {h: i for i, h in enumerate(hdr)}
    if len(rows) - 1 > 3_000_000:
        errs.append("more than 3,000,000 rows")
    pairs, noitem, kinds = Counter(), Counter(), {}
    seen = set()
    exc_count = Counter()
    for n, r in enumerate(rows[1:], start=2):
        t, it, am, ca, ex = (r[ix[k]].strip() for k in ("bank_txn_id", "item_id", "amount", "category", "exception"))
        if t not in phase_ids:
            errs.append(f"row {n}: bank_txn_id {t} not in phase")
        seen.add(t)
        if ex and ex not in EXCEPTIONS:
            errs.append(f"row {n}: bad exception {ex}")
        exc_count[ex or "(none)"] += 1
        if it:
            if not ITEM_RE.match(it):
                errs.append(f"row {n}: bad item_id {it}")
            try:
                if float(am) < 0:
                    errs.append(f"row {n}: negative amount")
            except ValueError:
                errs.append(f"row {n}: bad amount '{am}'")
            if ca:
                errs.append(f"row {n}: category on item row")
            pairs[(t, it)] += 1
            kinds.setdefault(t, set()).add("item")
        else:
            if am:
                errs.append(f"row {n}: amount without item")
            if ca not in cats:
                errs.append(f"row {n}: bad category '{ca}'")
            noitem[t] += 1
            kinds.setdefault(t, set()).add("cat")
        if len(errs) > 50:
            break
    errs += [f"duplicate pair {k}" for k, v in pairs.items() if v > 1][:10]
    errs += [f"several no-item rows for {k}" for k, v in noitem.items() if v > 1][:10]
    errs += [f"{k} mixes item rows and a category row" for k, v in kinds.items() if len(v) > 1][:10]
    missing = phase_ids - seen
    print(f"file {a.submission}: {size/1e6:.1f} MB on disk, {len(rows)-1} rows, {len(seen)} bank lines covered of {len(phase_ids)}")
    print(f"missing bank lines (count as wrong, not rejected): {len(missing)}")
    print("exceptions:", dict(exc_count.most_common()))
    if errs:
        print("INVALID:"); print("\n".join(errs[:60])); sys.exit(1)
    print("VALID")


if __name__ == "__main__":
    main()
