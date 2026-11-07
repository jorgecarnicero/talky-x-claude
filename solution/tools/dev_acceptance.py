"""Acceptance tests of the dev harness (run once after changing make_dev_dataset.py / dev.py / the scorer wiring).

    python solution/tools/dev_acceptance.py [--only oracle,unidentified,leak,start,determinism,baseline]

 oracle        golden_dev as the submission            -> must score 100
 unidentified  every line UNIDENTIFIED                 -> must score low
 leak          no date after the window start in train/, nothing outside the window in live/
 start         generated opening table at 2025-07-01 vs the real live/open_items_at_start of LIVE
 determinism   two pipeline runs (second one on warm caches) give byte-identical content
 baseline      score of the current checkout on the default window next to the LIVE score of model/v3 (82.25)
Needs DuckDB.
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import subprocess
import sys

import duckdb

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dev  # noqa: E402
import make_dev_dataset as M  # noqa: E402

D = dev.dataset_dir(*dev.DEFAULT, False)
SRC = os.path.expanduser("~/hackdata/data")
START, END = dev.DEFAULT
con = duckdb.connect()
results = []


def verdict(name, ok, detail):
    results.append((name, ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)


def one(sql):
    return con.execute(sql).fetchone()[0]


def t_oracle():
    sub = os.path.join(dev.ROOT, "dev_runs", "_oracle", "sub.csv.gz")
    os.makedirs(os.path.dirname(sub), exist_ok=True)
    with open(os.path.join(D, "golden_dev.csv"), newline="", encoding="utf-8") as fh, \
            gzip.open(sub, "wt", encoding="utf-8", newline="") as out:
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["bank_txn_id", "item_id", "amount", "category", "exception"])
        for r in csv.DictReader(fh):
            w.writerow([r["bank_txn_id"], r["item_id"], r["amount"], r["category"], r["exception"]])
    res = dev.score(D, sub, os.path.dirname(sub), None, {"test": "oracle", "window": f"{START}..{END}"})
    verdict("oracle", res["score"] == 100.0, f"score {res['score']}")


def t_unident():
    sub = os.path.join(dev.ROOT, "dev_runs", "_unidentified", "sub.csv.gz")
    os.makedirs(os.path.dirname(sub), exist_ok=True)
    with gzip.open(os.path.join(D, "live", "bank_transactions.csv.gz"), "rt", encoding="utf-8", newline="") as fh, \
            gzip.open(sub, "wt", encoding="utf-8", newline="") as out:
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["bank_txn_id", "item_id", "amount", "category", "exception"])
        for r in csv.DictReader(fh):
            w.writerow([r["bank_txn_id"], "", "", "UNIDENTIFIED", "UNIDENTIFIED"])
    res = dev.score(D, sub, os.path.dirname(sub), None, {"test": "all UNIDENTIFIED", "window": f"{START}..{END}"})
    verdict("unidentified", res["score"] < 20, f"score {res['score']} (alloc F1 {res['metrics']['allocation_f1']}, "
            f"txn acc {res['metrics']['txn_accuracy']})")


def t_leak():
    bad = []
    rd = M.rd
    p = lambda *a: os.path.join(D, *a)  # noqa: E731
    chk = [
        ("train bank lines < start", f"SELECT MAX(booking_date) FROM {rd(p('train', 'bank_transactions.csv.gz'))}", "<", START),
        ("live bank lines >= start", f"SELECT MIN(booking_date) FROM {rd(p('live', 'bank_transactions.csv.gz'))}", ">=", START),
        ("live bank lines <= end", f"SELECT MAX(booking_date) FROM {rd(p('live', 'bank_transactions.csv.gz'))}", "<=", END),
        ("open items issued < start", f"SELECT MAX(issue_date) FROM {rd(p('live', 'open_items_at_start.csv.gz'))}", "<", START),
    ]
    for f in M.DOCS:
        chk += [(f"train {f} < start", f"SELECT MAX(issue_date) FROM {rd(p('train', f))}", "<", START),
                (f"live {f} >= start", f"SELECT MIN(issue_date) FROM {rd(p('live', f))}", ">=", START),
                (f"live {f} <= end", f"SELECT MAX(issue_date) FROM {rd(p('live', f))}", "<=", END)]
    for sub, col in M.AUX.items():
        for side in ("train", "live"):
            d = p(side, sub)
            files = [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".csv")] if os.path.isdir(d) else []
            if not files:
                continue
            lst = "[" + ",".join(M.q(f) for f in files) + "]"
            sel = f"read_csv({lst}, header=true, all_varchar=true, union_by_name=true)"
            if side == "train":
                chk.append((f"train {sub}.{col} < start", f"SELECT MAX({col}) FROM {sel}", "<", START))
            else:
                chk.append((f"live {sub}.{col} >= start", f"SELECT MIN({col}) FROM {sel}", ">=", START))
                chk.append((f"live {sub}.{col} <= end", f"SELECT MAX({col}) FROM {sel}", "<=", END))
    chk += [("train advices received < start", f"SELECT MAX(received_date) FROM {rd(p('train', 'remittance_advices', 'index.csv'))}", "<", START),
            ("live advices received >= start", f"SELECT MIN(received_date) FROM {rd(p('live', 'remittance_advices', 'index.csv'))}", ">=", START),
            ("train pdfs document_date < start", f"SELECT MAX(document_date) FROM {rd(p('train', 'pdfs', 'index.csv'))}", "<", START),
            ("live pdfs document_date >= start", f"SELECT MIN(document_date) FROM {rd(p('live', 'pdfs', 'index.csv'))}", ">=", START),
            ("live pdfs document_date <= end", f"SELECT MAX(document_date) FROM {rd(p('live', 'pdfs', 'index.csv'))}", "<=", END),
            ("live advices received <= end", f"SELECT MAX(received_date) FROM {rd(p('live', 'remittance_advices', 'index.csv'))}", "<=", END)]
    for name, sql, op, ref in chk:
        v = one(sql)
        ok = v is not None and {"<": v < ref, "<=": v <= ref, ">=": v >= ref}[op]
        if not ok:
            bad.append(f"{name}: {v}")
    live_ids = f"(SELECT bank_txn_id FROM {rd(p('live', 'bank_transactions.csv.gz'))})"
    tr_ids = f"(SELECT bank_txn_id FROM {rd(p('train', 'bank_transactions.csv.gz'))})"
    n = one(f"SELECT COUNT(*) FROM {rd(p('train', 'golden_allocations.csv.gz'))} WHERE bank_txn_id NOT IN {tr_ids}")
    if n:
        bad.append(f"train golden has {n} rows of non-train lines")
    n = one(f"SELECT COUNT(*) FROM {rd(p('train', 'golden_allocations.csv.gz'))} WHERE bank_txn_id IN {live_ids}")
    if n:
        bad.append(f"train golden contains {n} rows of window lines")
    n = one(f"SELECT COUNT(*) FROM {rd(p('golden_dev.csv'))} WHERE bank_txn_id NOT IN {live_ids}")
    if n:
        bad.append(f"golden_dev has {n} rows outside the window")
    n = one(f"SELECT COUNT(*) FROM {live_ids} WHERE bank_txn_id IN {tr_ids}")
    if n:
        bad.append(f"{n} bank lines are in both train and live")
    # every advice listed exists; no advice file on disk outside its index
    for side, sub in [(s_, u_) for s_ in ("train", "live") for u_ in ("remittance_advices", "pdfs")]:
        base = p(side, sub)
        listed = {r[0] for r in con.execute(f"SELECT file FROM {rd(os.path.join(base, 'index.csv'))}").fetchall()}
        on_disk = {os.path.join(e, f) for e in os.listdir(base) if os.path.isdir(os.path.join(base, e))
                   for f in os.listdir(os.path.join(base, e))}
        if listed != on_disk:
            bad.append(f"{side} {sub}: {len(listed ^ on_disk)} files differ from the index")
    verdict("leak", not bad, f"{len(chk) + 5} checks" + ("" if not bad else "; " + " | ".join(bad)))


def t_start():
    out = os.path.join(os.path.dirname(HERE), "..", "dev_runs", "_start_check.csv")
    real = f"SELECT * FROM {M.rd(os.path.join(SRC, 'live', 'open_items_at_start.csv.gz'))}"
    gen = M.open_items_relation(con, SRC, "2025-07-01", exclude_never_settled=False)  # LIVE labels do not exist yet
    con.execute(f"CREATE OR REPLACE TABLE real AS {real}")
    con.execute(f"CREATE OR REPLACE TABLE gen AS {gen}")
    nr, ng = one("SELECT COUNT(*) FROM real"), one("SELECT COUNT(*) FROM gen")
    both = one("SELECT COUNT(*) FROM real JOIN gen USING (item_id)")
    same_open = one("SELECT COUNT(*) FROM real r JOIN gen g USING (item_id) WHERE ABS(CAST(r.open_amount AS DOUBLE) - CAST(g.open_amount AS DOUBLE)) < 0.015")
    print("   type counts   real vs generated")
    for t, a, b in con.execute("""SELECT COALESCE(r.item_type, g.item_type), COALESCE(r.n, 0), COALESCE(g.n, 0) FROM
        (SELECT item_type, COUNT(*) n FROM real GROUP BY 1) r FULL JOIN (SELECT item_type, COUNT(*) n FROM gen GROUP BY 1) g
        ON r.item_type = g.item_type ORDER BY 1""").fetchall():
        print(f"     {t}: {a:>7} vs {b:>7}")
    print("   issue year    real vs generated")
    for y, a, b in con.execute("""SELECT COALESCE(r.y, g.y), COALESCE(r.n, 0), COALESCE(g.n, 0) FROM
        (SELECT substr(issue_date,1,4) y, COUNT(*) n FROM real GROUP BY 1) r FULL JOIN
        (SELECT substr(issue_date,1,4) y, COUNT(*) n FROM gen GROUP BY 1) g ON r.y = g.y ORDER BY 1""").fetchall():
        print(f"     {y}: {a:>7} vs {b:>7}")
    only_real = con.execute("""SELECT r.item_type, COUNT(*) FROM real r ANTI JOIN gen g USING (item_id) GROUP BY 1 ORDER BY 1""").fetchall()
    only_gen = con.execute("""SELECT g.item_type, COUNT(*) FROM gen g ANTI JOIN real r USING (item_id) GROUP BY 1 ORDER BY 1""").fetchall()
    print(f"   only in real: {only_real}   only in generated: {only_gen}")
    rec, prec = both / nr, both / ng
    # shape of the clean dev opening (rule: pending balance AND settled by a bank line somewhere in TRAIN)
    dev_oi = f"SELECT * FROM {M.rd(os.path.join(D, 'live', 'open_items_at_start.csv.gz'))}"
    print("   shape of the dev opening (2025-04-01): type counts, issue years")
    print("    ", con.execute(f"SELECT item_type, COUNT(*) FROM ({dev_oi}) GROUP BY 1 ORDER BY 1").fetchall())
    print("    ", con.execute(f"SELECT substr(issue_date,1,4), COUNT(*) FROM ({dev_oi}) GROUP BY 1 ORDER BY 1").fetchall())
    verdict("start", rec >= 0.99 and same_open / max(both, 1) >= 0.99,
            f"real LIVE opening {nr}: all-open-per-golden rule at 2025-07-01 recovers {both} (recall {rec:.3f}), same open "
            f"amount on {same_open / max(both, 1):.3f}; the extra {ng - both} are mostly old items that never settle "
            f"(excluded by the clean start)")


def sha(path):
    h = hashlib.sha256()
    with gzip.open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def t_determinism():
    hs = []
    for i, fresh in ((1, True), (2, False)):
        rd_ = os.path.join(dev.ROOT, "dev_runs", f"_det{i}")
        os.makedirs(rd_, exist_ok=True)
        sub, _, secs = dev.run_pipeline(D, rd_, fresh)
        hs.append((sha(sub), secs))
    verdict("determinism", hs[0][0] == hs[1][0], f"cold {hs[0][0]} ({hs[0][1]:.0f}s) vs warm {hs[1][0]} ({hs[1][1]:.0f}s)")


def t_baseline():
    sub, why, secs = dev.run_pipeline(D, os.path.join(dev.ROOT, "dev_runs", "_baseline"), False)
    res = dev.score(D, sub, os.path.join(dev.ROOT, "dev_runs", "_baseline"), why, {"test": "baseline", "window": f"{START}..{END}"})
    verdict("baseline", abs(res["score"] - 82.25) < 3.0,
            f"this checkout on the sim: {res['score']:.2f}  (model/v3 LIVE 82.25, old --phase dev 82.90; {secs:.0f}s)")


ALL = {"oracle": t_oracle, "unidentified": t_unident, "leak": t_leak, "start": t_start, "determinism": t_determinism,
       "baseline": t_baseline}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=",".join(ALL))
    for k in ap.parse_args().only.split(","):
        ALL[k]()
    sys.exit(0 if all(ok for _, ok in results) else 1)
