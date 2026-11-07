"""Synthetic FINAL phase for the robustness smoke test (docs/PROTOCOL.md step 3): a new entity + a new currency.

    python src/synth_final.py --out ../out/synth          # then BANKREC_DATA=../out/synth ... --phase final

Builds <out>/ with masters/, train/, live/, starter/ linked to the real data (directory junctions, nothing copied) and
a fake final/ made of the LIVE lines booked from --since on, where entity PL01 becomes QA01 and currency PLN becomes
QAR (no FX rate exists for QAR). final/masters_final/ holds ONLY the changed master files (bank_accounts, entities,
customers, suppliers with the QA01 rows added), like a partial delivery. The run must finish without errors and
produce a valid file; scores are meaningless here.
"""
import argparse
import csv
import gzip
import os
import shutil
import subprocess

ap = argparse.ArgumentParser()
ap.add_argument("--data", default=os.environ.get("BANKREC_DATA", ""))
ap.add_argument("--out", required=True)
ap.add_argument("--since", default="2025-12-15")
ap.add_argument("--old", default="PL01")
ap.add_argument("--new", default="QA01")
ap.add_argument("--old-ccy", default="PLN")
ap.add_argument("--new-ccy", default="QAR")
a = ap.parse_args()
src = os.path.abspath(a.data)
out = os.path.abspath(a.out)
os.makedirs(out, exist_ok=True)


def link(name):
    dst = os.path.join(out, name)
    if not os.path.exists(dst):
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", dst, os.path.join(src, name)], check=True,
                           stdout=subprocess.DEVNULL)
        else:
            os.symlink(os.path.join(src, name), dst)


for d in ("masters", "train", "live", "starter"):
    link(d)


def sub(v):
    return v.replace(a.old, a.new).replace(a.old_ccy, a.new_ccy) if isinstance(v, str) else v


def rewrite(path_in, path_out, keep=lambda r: True, gz=True):
    op = gzip.open if gz else open
    with op(path_in, "rt", encoding="utf-8", newline="") as fh, op(path_out, "wt", encoding="utf-8", newline="") as fo:
        rd = csv.DictReader(fh)
        w = csv.DictWriter(fo, rd.fieldnames, lineterminator="\n")
        w.writeheader()
        n = 0
        for r in rd:
            if keep(r):
                w.writerow({k: sub(v) for k, v in r.items()})
                n += 1
    return n


fin = os.path.join(out, "final")
if os.path.exists(fin):
    shutil.rmtree(fin)
os.makedirs(os.path.join(fin, "masters_final"))
live = os.path.join(src, "live")
n = rewrite(os.path.join(live, "bank_transactions.csv.gz"), os.path.join(fin, "bank_transactions.csv.gz"),
            keep=lambda r: r["booking_date"] >= a.since)
for f in ("ar_invoices", "ap_invoices", "credit_notes", "open_items_at_start"):
    rewrite(os.path.join(live, f + ".csv.gz"), os.path.join(fin, f + ".csv.gz"))
for d in ("direct_debit_batches", "card_settlements", "cheque_deposits", "payment_runs", "remittance_advices", "pdfs"):
    if os.path.isdir(os.path.join(live, d)):
        shutil.copytree(os.path.join(live, d), os.path.join(fin, d), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("._*"))
# partial masters_final: only the changed files, with the new entity's rows ADDED (old rows kept)
for f in ("bank_accounts", "entities", "customers", "suppliers"):
    p = os.path.join(src, "masters", f + ".csv")
    with open(p, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
        cols = list(rows[0].keys()) if rows else []
    extra = [{k: sub(v) for k, v in r.items()} for r in rows if a.old in "".join(map(str, r.values()))]
    with open(os.path.join(fin, "masters_final", f + ".csv"), "w", encoding="utf-8", newline="") as fo:
        w = csv.DictWriter(fo, cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows + [r for r in extra if r not in rows])
print(f"{out}: final = {n} LIVE lines since {a.since}; {a.old}/{a.old_ccy} -> {a.new}/{a.new_ccy}")
