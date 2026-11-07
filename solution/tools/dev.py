"""Faithful LIVE simulation on TRAIN: build the dev dataset if missing, run the UNCHANGED pipeline in --phase live, score it
with the official scorer against golden_dev.csv and write dev_runs/<name>/.

    python solution/tools/dev.py                      # window 2025-04-01..2025-05-31, name = current git branch
    python solution/tools/dev.py --name my-idea
    python solution/tools/dev.py --sealed             # 2025-01-01..2025-02-28 (sealed holdout: use sparingly!)
    python solution/tools/dev.py --score-only sub.csv.gz --name oracle     # score an existing submission, no pipeline run

Outputs in dev_runs/<name>/: report.md, result.json, segments.csv, classes.csv, failures_<segment>.csv,
sub.csv.gz, why.csv. Compare two runs with solution/tools/compare.py.
Window datasets: ~/hackdata/data_dev (default window), ~/hackdata/data_dev_sealed, ~/hackdata/data_dev_<start>_<end>.
The dataset is built with DuckDB (pip install duckdb) the first time or with --rebuild; the pipeline needs no DuckDB.
"""
import argparse
import csv
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "starter"))
import bankrec_score as S  # noqa: E402

DEFAULT = ("2025-04-01", "2025-05-31")
SEALED = ("2025-01-01", "2025-02-28")
EXC = ["PARTIAL", "OVERPAYMENT", "SHORT_DISCOUNT", "SHORT_BANK_CHARGES", "SHORT_WITHHOLDING", "SHORT_DISPUTE",
       "DUPLICATE_PAYMENT", "UNIDENTIFIED", "RETURNED", "FX_DIFFERENCE"]
SMALL_N = 100  # classes with fewer golden rows are flagged as unreliable


def overlaps(a, b):
    return not (a[1] < b[0] or b[1] < a[0])


def git(*args):
    try:
        return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def dataset_dir(start, end, sealed, out=None):
    if out:
        return os.path.abspath(os.path.expanduser(out))
    base = os.path.expanduser("~/hackdata")
    if (start, end) == DEFAULT:
        return os.path.join(base, "data_dev")
    if (start, end) == SEALED:
        return os.path.join(base, "data_dev_sealed")
    return os.path.join(base, f"data_dev_{start}_{end}")


def ensure_dataset(d, start, end, src, rebuild):
    wj = os.path.join(d, "window.json")
    ok = False
    if os.path.exists(wj) and not rebuild:
        w = json.load(open(wj))
        ok = (w["start"], w["end"]) == (start, end)
    if ok:
        return json.load(open(wj))
    print(f"[dev] building dataset {d} ({start}..{end}) ...", flush=True)
    r = subprocess.run([sys.executable, os.path.join(HERE, "make_dev_dataset.py"), "--start", start, "--end", end,
                        "--out", d, "--src", src])
    if r.returncode:
        sys.exit("dataset build failed (is DuckDB installed? pip install duckdb)")
    return json.load(open(wj))


def run_pipeline(d, run_dir, fresh):
    if fresh:
        shutil.rmtree(os.path.join(d, "solution", "cache"), ignore_errors=True)
    os.makedirs(run_dir, exist_ok=True)
    sub, why = os.path.join(run_dir, "sub.csv.gz"), os.path.join(run_dir, "why.csv")
    t0 = time.time()
    r = subprocess.run([sys.executable, os.path.join(ROOT, "solution", "bankrec.py"), "--phase", "live", "--data", d,
                        "--out", sub, "--why", why],
                       env={**os.environ, "PYTHONHASHSEED": "0"})  # the matcher iterates over sets: pin the hash order
    if r.returncode:
        sys.exit("pipeline failed")
    return sub, why, time.time() - t0


def read_sub(path):
    op = gzip.open if path.endswith(".gz") else open
    pred = {}  # txn -> {"items": {item: (amt, exc)}, "cat": None, "exc": None}
    with op(path, "rt", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            p = pred.setdefault(r["bank_txn_id"], {"items": {}, "cat": None, "exc": None})
            if r["item_id"]:
                p["items"][r["item_id"]] = (float(r["amount"]), r["exception"].upper())
            else:
                p["cat"], p["exc"] = r["category"].upper(), r["exception"].upper()
    return pred


def txn_ok(g, p):
    gitems, gcat, gexc, _ = g
    if p is None:
        return False
    if gitems:
        if p["cat"] is not None or len(p["items"]) != len(gitems):
            return False
        return all(i in gitems and abs(a - gitems[i][0]) <= S._tol(gitems[i][0]) and e == gitems[i][1]
                   for i, (a, e) in p["items"].items())
    return p["cat"] is not None and not p["items"] and p["cat"] == gcat and p["exc"] == gexc


def fmt_g(g):
    gitems, gcat, gexc, _ = g
    return "; ".join(f"{i}:{a:.2f}:{e}" for i, (a, e) in sorted(gitems.items())) if gitems else f"-:{gcat}:{gexc}"


def fmt_p(p):
    if p is None:
        return "(missing)"
    if p["cat"] is not None:
        return f"-:{p['cat']}:{p['exc']}"
    return "; ".join(f"{i}:{a:.2f}:{e}" for i, (a, e) in sorted(p["items"].items()))


def class_stats(golden, pred):
    tp = {c: 0 for c in EXC}
    sub = {c: 0 for c in EXC}
    gold = {c: 0 for c in EXC}
    for t, (gitems, gcat, gexc, _) in golden.txns.items():
        if gitems:
            for _, e in gitems.values():
                if e in gold:
                    gold[e] += 1
        elif gexc in gold:
            gold[gexc] += 1
        p = pred.get(t)
        if not p:
            continue
        for i, (a, e) in p["items"].items():
            if e in sub:
                sub[e] += 1
                gi = gitems.get(i)
                if gi is not None and gi[1] == e:
                    tp[e] += 1
        if p["cat"] is not None and p["exc"] in sub:
            sub[p["exc"]] += 1
            if not gitems and gexc == p["exc"]:
                tp[p["exc"]] += 1
    rows = []
    for c in EXC:
        P = tp[c] / sub[c] if sub[c] else 0.0
        R = tp[c] / gold[c] if gold[c] else 0.0
        F = 2 * P * R / (P + R) if P + R else 0.0
        rows.append({"class": c, "pred_rows": sub[c], "golden_rows": gold[c], "tp": tp[c], "precision": round(P, 4),
                     "recall": round(R, 4), "f1": round(F, 4), "note": "(poco fiable: n<100)" if gold[c] < SMALL_N else ""})
    return rows


def score(d, sub_path, run_dir, why_path=None, meta=None):
    live_bt = os.path.join(d, "live", "bank_transactions.csv.gz")
    cats = [r["category"] for r in csv.DictReader(open(os.path.join(d, "masters", "categories.csv"), encoding="utf-8"))]
    golden = S.load_golden(os.path.join(d, "golden_dev.csv"), live_bt, EXC, cats)
    res = S.score_submission(golden, open(sub_path, "rb").read(), sub_path)
    if not res["ok"]:
        sys.exit("official scorer rejected the submission: " + json.dumps(res["errors"][:5]))
    pred = read_sub(sub_path)
    why = {}
    if why_path and os.path.exists(why_path):
        with open(why_path, newline="", encoding="utf-8") as fh:
            why = {r["bank_txn_id"]: r["why"] for r in csv.DictReader(fh)}
    info = {}
    with gzip.open(live_bt, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            info[r["bank_txn_id"]] = (r["booking_date"], r["amount"], r["currency"], r["text"], r["counterparty_name"])

    seg_fail = {}
    for t, g in golden.txns.items():
        if not txn_ok(g, pred.get(t)):
            seg_fail.setdefault(g[3], []).append(t)
    os.makedirs(run_dir, exist_ok=True)
    for f in os.listdir(run_dir):
        if f.startswith("failures_"):
            os.remove(os.path.join(run_dir, f))
    total = sum(v["n"] for v in res["segments"].values())
    seg_rows = []
    for k, v in res["segments"].items():
        seg_rows.append({"segment": k, "n": v["n"], "share": round(v["n"] / total, 4), "accuracy": v["accuracy"],
                         "wrong": len(seg_fail.get(k, [])), "lost_points": round(100 * len(seg_fail.get(k, [])) / total, 3)})
    seg_rows.sort(key=lambda r: -r["wrong"])
    with open(os.path.join(run_dir, "segments.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, list(seg_rows[0]))
        w.writeheader()
        w.writerows(seg_rows)
    for k, ts in seg_fail.items():
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", k)
        with open(os.path.join(run_dir, f"failures_{safe}.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["bank_txn_id", "booking_date", "amount", "currency", "text", "counterparty", "golden",
                        "predicted", "matcher"])
            for t in sorted(ts):
                i = info.get(t, ("", "", "", "", ""))
                w.writerow([t, i[0], i[1], i[2], i[3], i[4], fmt_g(golden.txns[t]), fmt_p(pred.get(t)), why.get(t, "")])
    cls = class_stats(golden, pred)
    with open(os.path.join(run_dir, "classes.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, list(cls[0]))
        w.writeheader()
        w.writerows(cls)
    out = {"score": res["score"], "metrics": res["metrics"], "meta": meta or {},
           "segments": {r["segment"]: r["accuracy"] for r in seg_rows},
           "classes": {r["class"]: {"f1": r["f1"], "support": r["golden_rows"]} for r in cls}}
    json.dump(out, open(os.path.join(run_dir, "result.json"), "w"), indent=1)

    m = res["metrics"]
    L = [f"# Dev run `{os.path.basename(run_dir)}`", "",
         f"**Score {res['score']:.2f}**  (alloc F1 {m['allocation_f1']:.4f} · P {m['precision']:.4f} · R {m['recall']:.4f} · "
         f"txn acc {m['txn_accuracy']:.4f} · exception macro-F1 {m['exception_macro_f1']:.4f})", ""]
    for k, v in (meta or {}).items():
        L.append(f"- {k}: {v}")
    L += ["", f"Bank lines: {m['txns_total']}, covered {m['txns_covered']}, rows {m['rows']}", "",
          "## Segments by lost lines", "", "| segment | n | accuracy | wrong | lost pts |", "|---|---:|---:|---:|---:|"]
    for r in seg_rows[:25]:
        L.append(f"| {r['segment']} | {r['n']} | {r['accuracy']:.3f} | {r['wrong']} | {r['lost_points']:.2f} |")
    L += ["", "## Exception classes", "", "| class | pred | golden | precision | recall | F1 | |", "|---|---:|---:|---:|---:|---:|---|"]
    for r in cls:
        L.append(f"| {r['class']} | {r['pred_rows']} | {r['golden_rows']} | {r['precision']:.3f} | {r['recall']:.3f} | "
                 f"{r['f1']:.3f} | {r['note']} |")
    open(os.path.join(run_dir, "report.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default=None, help="run name (default: current git branch)")
    ap.add_argument("--start", default=DEFAULT[0])
    ap.add_argument("--end", default=DEFAULT[1])
    ap.add_argument("--sealed", action="store_true", help="use the sealed window 2025-01-01..2025-02-28")
    ap.add_argument("--out", default=None, help="dataset directory (default: ~/hackdata/data_dev*, one per window); "
                    "use your own to run a different window in parallel without touching data_dev")
    ap.add_argument("--src", default=os.path.expanduser("~/hackdata/data"), help="extracted hackathon data (train/, masters/)")
    ap.add_argument("--rebuild", action="store_true", help="rebuild the dev dataset")
    ap.add_argument("--fresh", action="store_true", help="delete the pipeline caches (PDF advices, items, category model)")
    ap.add_argument("--score-only", default=None, metavar="SUB", help="score this submission instead of running the pipeline")
    a = ap.parse_args()
    start, end = (SEALED if a.sealed else (a.start, a.end))
    if not a.sealed and overlaps((start, end), SEALED):
        sys.exit(f"window {start}..{end} overlaps the sealed holdout {SEALED[0]}..{SEALED[1]}: pass --sealed to use it")
    if a.sealed and (a.start, a.end) != DEFAULT:
        sys.exit("--sealed fixes the window; do not combine with --start/--end")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", a.name or git("rev-parse", "--abbrev-ref", "HEAD") or "run")
    run_dir = os.path.join(ROOT, "dev_runs", name)
    os.makedirs(run_dir, exist_ok=True)
    d = dataset_dir(start, end, a.sealed, a.out)
    win = ensure_dataset(d, start, end, a.src, a.rebuild)
    meta = {"window": f"{start}..{end}", "dataset": d, "git": f"{git('rev-parse', '--short', 'HEAD')} "
            f"({git('rev-parse', '--abbrev-ref', 'HEAD')})" + (" +uncommitted changes" if git("status", "--porcelain", "--untracked-files=no") else ""),
            "opening items": win["open_items"], "date": date.today().isoformat()}
    if a.score_only:
        sub, why, secs = a.score_only, None, None
    else:
        if not shutil.which("pdftotext"):
            print("[dev] WARNING: pdftotext not on PATH, PDF advices skipped: scores are NOT comparable with runs that parse them")
            meta["pdftotext"] = "MISSING"
        sub, why, secs = run_pipeline(d, run_dir, a.fresh)
        meta["pipeline seconds"] = round(secs, 1)
    t0 = time.time()
    out = score(d, sub, run_dir, why, meta)
    print("[dev] exception classes (F1 / golden support):")
    for c, v in out["classes"].items():
        print(f"   {c:20s} F1 {v['f1']:.3f}  n={v['support']:>6}{'   (poco fiable: n<100)' if v['support'] < SMALL_N else ''}")
    print(f"[dev] {name}: score {out['score']:.2f}  ({json.dumps({k: out['metrics'][k] for k in ('allocation_f1', 'txn_accuracy', 'exception_macro_f1')})})"
          f"  scored in {time.time() - t0:.0f}s -> {run_dir}/report.md")


if __name__ == "__main__":
    main()
