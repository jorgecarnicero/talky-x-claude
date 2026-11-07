"""Build a LIVE-shaped dev dataset out of TRAIN, so that the unchanged pipeline can be run as a faithful LIVE simulation:

    python solution/bankrec.py --phase live --data ~/hackdata/data_dev

    python solution/tools/make_dev_dataset.py --start 2025-04-01 --end 2025-05-31 --out ~/hackdata/data_dev [--src ~/hackdata/data]

Layout written to --out (never inside the repo):
  live/     bank lines, AR/AP invoices, credit notes issued in [start, end]; batches / cheques / card settlements /
            payment runs dated in the window; remittance advices (+ index.csv) received in the window; and
            open_items_at_start.csv.gz = clean opening balance (items issued before `start`, still open at `start`
            according to the golden, excluding items that are never settled by a bank line anywhere in TRAIN).
  train/    ONLY data before `start` (bank lines, documents, batches, advices, golden_allocations).
  masters/  copied.      golden_dev.csv = golden rows of the window's bank lines (scored by tools/dev.py).

Needs DuckDB (pip install duckdb).
"""
import argparse
import calendar
import json
import os
import re
import shutil
import sys
from datetime import date, timedelta

try:
    import duckdb
except ImportError:
    sys.exit("DuckDB is required: pip install duckdb")

AUX = {  # folder -> date column used to cut files that straddle the window
    "direct_debit_batches": "collection_date",
    "card_settlements": "settlement_date",
    "cheque_deposits": "deposit_date",
    "payment_runs": "execution_date",
}
DOCS = {"ar_invoices.csv.gz": "issue_date", "ap_invoices.csv.gz": "issue_date", "credit_notes.csv.gz": "issue_date"}


def q(p):
    return "'" + str(p).replace("'", "''") + "'"


def rd(path):
    return f"read_csv({q(path)}, header=true, all_varchar=true, sample_size=-1)"


def copy_query(con, sql, out):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    comp = ", COMPRESSION gzip" if out.endswith(".gz") else ""
    con.execute(f"COPY ({sql}) TO {q(out)} (FORMAT csv, HEADER{comp})")


def link_or_copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def open_items_relation(con, src, start, exclude_never_settled=True):
    """Items issued before `start` and still open at `start` (per the TRAIN golden), as a DuckDB relation SQL.
    Invoices that are never settled by a bank line anywhere in TRAIN are excluded (clean start). Credit notes are all
    kept: real LIVE keeps ~60 % of its open credit notes (they are consumed late, by n-to-m payments), whereas the
    "settled in TRAIN" rule would keep only 3-18 % of them and make the simulation easier than LIVE."""
    t = os.path.join(src, "train")
    return f"""
    WITH tx AS (SELECT bank_txn_id FROM {rd(os.path.join(t, 'bank_transactions.csv.gz'))}
                WHERE booking_date < '{start}'),
    g AS (SELECT * FROM {rd(os.path.join(t, 'golden_allocations.csv.gz'))} WHERE item_id <> ''),
    paid AS (SELECT item_id, SUM(CASE WHEN exception = 'RETURNED' THEN -CAST(amount AS DECIMAL(18,2))
                                      WHEN exception = 'DUPLICATE_PAYMENT' THEN 0
                                      ELSE CAST(amount AS DECIMAL(18,2)) END) AS paid
             FROM g WHERE bank_txn_id IN (SELECT bank_txn_id FROM tx) GROUP BY item_id),
    ever AS (SELECT DISTINCT item_id FROM g WHERE COALESCE(exception, '') NOT IN ('RETURNED', 'DUPLICATE_PAYMENT')),
    items AS (
      SELECT item_id, 'ARI' AS item_type, entity, customer_id AS party_id, document_number, issue_date, due_date,
             currency, total_amount FROM {rd(os.path.join(t, 'ar_invoices.csv.gz'))}
      UNION ALL
      SELECT item_id, 'API', entity, supplier_id, supplier_invoice_number, issue_date, due_date, currency, total_amount
        FROM {rd(os.path.join(t, 'ap_invoices.csv.gz'))}
      UNION ALL
      SELECT item_id, side || 'C', entity, party_id, document_number, issue_date,
             CAST(CAST(issue_date AS DATE) + 30 AS VARCHAR), currency, total_amount
        FROM {rd(os.path.join(t, 'credit_notes.csv.gz'))})
    SELECT i.item_id, i.item_type, i.entity, i.party_id, i.document_number, i.issue_date, i.due_date, i.currency,
           i.total_amount,
           CAST(CAST(i.total_amount AS DECIMAL(18,2)) - COALESCE(p.paid, 0) AS DECIMAL(18,2)) AS open_amount
    FROM items i LEFT JOIN paid p USING (item_id) LEFT JOIN ever e USING (item_id)
    WHERE i.issue_date < '{start}' AND CAST(i.total_amount AS DECIMAL(18,2)) - COALESCE(p.paid, 0) > 0
      {"AND (i.item_type IN ('ARC', 'APC') OR e.item_id IS NOT NULL)" if exclude_never_settled else ""}
    ORDER BY i.item_id"""


def month_bounds(ym):
    y, m = int(ym[:4]), int(ym[4:])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def split_aux(con, src, out, sub, col, start, end):
    n_tr = n_lv = 0
    sd = os.path.join(src, "train", sub)
    if not os.path.isdir(sd):
        return 0, 0
    for name in sorted(os.listdir(sd)):
        if name.startswith("._") or not name.endswith(".csv"):
            continue
        m = re.search(r"_(\d{6})", name)
        if not m:
            continue
        lo, hi = month_bounds(m.group(1))
        f = os.path.join(sd, name)
        if hi < start:
            link_or_copy(f, os.path.join(out, "train", sub, name))
            n_tr += 1
        elif lo > end:
            continue
        elif lo >= start and hi <= end:
            link_or_copy(f, os.path.join(out, "live", sub, name))
            n_lv += 1
        else:  # straddles a boundary: cut by row date
            for side, cond in (("train", f"{col} < '{start}'"), ("live", f"{col} >= '{start}' AND {col} <= '{end}'")):
                tgt = os.path.join(out, side, sub, name)
                copy_query(con, f"SELECT * FROM {rd(f)} WHERE {cond}", tgt)
                if con.execute(f"SELECT COUNT(*) FROM {rd(tgt)}").fetchone()[0] == 0:
                    os.remove(tgt)
                elif side == "train":
                    n_tr += 1
                else:
                    n_lv += 1
    return n_tr, n_lv


def split_advices(con, src, out, start, end):
    base = os.path.join(src, "train", "remittance_advices")
    idx = os.path.join(base, "index.csv")
    if not os.path.exists(idx):
        return 0, 0
    cnt = {"train": 0, "live": 0}
    for side, cond in (("train", f"received_date < '{start}'"),
                       ("live", f"received_date >= '{start}' AND received_date <= '{end}'")):
        tgt = os.path.join(out, side, "remittance_advices", "index.csv")
        copy_query(con, f"SELECT * FROM {rd(idx)} WHERE {cond} ORDER BY file", tgt)
        for (fn,) in con.execute(f"SELECT file FROM {rd(tgt)}").fetchall():
            p = os.path.join(base, fn)
            if os.path.exists(p):
                link_or_copy(p, os.path.join(out, side, "remittance_advices", fn))
                cnt[side] += 1
    return cnt["train"], cnt["live"]


def build(src, out, start, end):
    src, out = os.path.abspath(os.path.expanduser(src)), os.path.abspath(os.path.expanduser(out))
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if out == repo or out.startswith(repo + os.sep):
        sys.exit("refusing to write the dev dataset inside the repo")
    if out == src or not start < end:
        sys.exit("--out must differ from --src and start < end")
    for sub in ("train", "live", "masters"):
        shutil.rmtree(os.path.join(out, sub), ignore_errors=True)
    shutil.rmtree(os.path.join(out, "solution", "cache"), ignore_errors=True)  # caches depend on the dataset
    for f in ("golden_dev.csv", "window.json"):
        if os.path.exists(os.path.join(out, f)):
            os.remove(os.path.join(out, f))
    os.makedirs(out, exist_ok=True)
    con = duckdb.connect()
    T, L = os.path.join(src, "train"), os.path.join(out, "live")
    O_T = os.path.join(out, "train")
    s, e = start.isoformat(), end.isoformat()

    # ---- bank lines and documents
    bt = rd(os.path.join(T, "bank_transactions.csv.gz"))
    copy_query(con, f"SELECT * FROM {bt} WHERE booking_date < '{s}'", os.path.join(O_T, "bank_transactions.csv.gz"))
    copy_query(con, f"SELECT * FROM {bt} WHERE booking_date >= '{s}' AND booking_date <= '{e}' ORDER BY bank_txn_id",
               os.path.join(L, "bank_transactions.csv.gz"))
    for fn, col in DOCS.items():
        r = rd(os.path.join(T, fn))
        copy_query(con, f"SELECT * FROM {r} WHERE {col} < '{s}'", os.path.join(O_T, fn))
        copy_query(con, f"SELECT * FROM {r} WHERE {col} >= '{s}' AND {col} <= '{e}'", os.path.join(L, fn))

    # ---- golden: train keeps only bank lines before the window; golden_dev.csv is the window's golden
    g = rd(os.path.join(T, "golden_allocations.csv.gz"))
    before = f"(SELECT bank_txn_id FROM {rd(os.path.join(O_T, 'bank_transactions.csv.gz'))})"
    inwin = f"(SELECT bank_txn_id FROM {rd(os.path.join(L, 'bank_transactions.csv.gz'))})"
    copy_query(con, f"SELECT * FROM {g} WHERE bank_txn_id IN {before}", os.path.join(O_T, "golden_allocations.csv.gz"))
    copy_query(con, f"SELECT * FROM {g} WHERE bank_txn_id IN {inwin} ORDER BY bank_txn_id",
               os.path.join(out, "golden_dev.csv"))

    # ---- clean opening balance
    copy_query(con, open_items_relation(con, src, s), os.path.join(L, "open_items_at_start.csv.gz"))

    # ---- batches, cheques, cards, payment runs, advices
    counts = {}
    for sub, col in AUX.items():
        counts[sub] = split_aux(con, src, out, sub, col, start, end)
    counts["remittance_advices"] = split_advices(con, src, out, start, end)

    # ---- masters
    shutil.copytree(os.path.join(src, "masters"), os.path.join(out, "masters"))

    stats = {"start": s, "end": e, "src": src, "files_train_live": counts}
    for name, path in (("live_txns", os.path.join(L, "bank_transactions.csv.gz")),
                       ("open_items", os.path.join(L, "open_items_at_start.csv.gz")),
                       ("golden_dev_rows", os.path.join(out, "golden_dev.csv"))):
        stats[name] = con.execute(f"SELECT COUNT(*) FROM {rd(path)}").fetchone()[0]
    with open(os.path.join(out, "window.json"), "w") as fh:
        json.dump(stats, fh, indent=1)
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--src", default=os.path.expanduser("~/hackdata/data"), help="folder with train/ and masters/")
    a = ap.parse_args()
    st = build(a.src, a.out, date.fromisoformat(a.start), date.fromisoformat(a.end))
    print(json.dumps(st, indent=1))


if __name__ == "__main__":
    main()
