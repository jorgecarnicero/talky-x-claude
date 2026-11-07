"""Pre-flight / post-flight checks for a LIVE or FINAL run. Read-only; exit code 1 if any check FAILS.

    python solution/tools/final_check.py --phase final --data ~/hackdata/data               # before the run
    python solution/tools/final_check.py --phase final --data ... --sub sub_final_final.csv.gz  # after it

Checks: clean git tree + hash; solution/run_final.sh and solution/cascade.conf exist and equal run_v6c.sh's chain and
env + exc_overpay; masters_final has every table/column of masters/; pdftotext on PATH; PYTHONHASHSEED pinned; items
loaded vs open_items_at_start (--skip-ledger to skip, loads the phase ~1 min); with --sub: src/validate_submission.py
plus the share of UNIDENTIFIED lines per entity.
"""
import argparse
import csv
import gzip
import os
import re
import shutil
import subprocess
import sys
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
RES = []


def check(name, ok, detail=""):
    RES.append(ok)
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""), flush=True)


def git(*a):
    return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True).stdout.strip()


def conf(path):
    kv = {}
    for l in open(path, encoding="utf-8"):
        m = re.match(r"^([A-Z_0-9]+)=(.*)$", l.strip())
        if m:
            kv[m.group(1)] = m.group(2)
    return kv


def v6b_chain(path):
    s = open(path, encoding="utf-8").read()
    passes = re.search(r"--passes\s+(\S+)", s).group(1).split(",")
    env = dict(re.findall(r"\b([A-Z][A-Z_0-9]+)=(\S+)", " ".join(l for l in s.splitlines() if "pipeline.py" in l)))
    env.pop("V3_SUB", None)
    # on by default in the v6c code, listed explicitly in cascade.conf; EXC_OVERPAY=1 is the exc_overpay switch
    env.update({"AR_CNREL": "1", "AR_ZREF": "1", "MULTI_FX": "1", "EXC_OVERPAY": "1"})
    return passes, env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="final", choices=["live", "final"])
    ap.add_argument("--data", default=os.path.expanduser("~/hackdata/data"))
    ap.add_argument("--sub")
    ap.add_argument("--skip-ledger", action="store_true")
    a = ap.parse_args()
    # 1. git
    st = git("status", "--porcelain")
    check("git tree clean", not st, f"HEAD {git('rev-parse', 'HEAD')}" + (f"; dirty: {len(st.splitlines())} paths" if st else ""))
    # 2. chain
    rf, cc, v6 = (os.path.join(ROOT, p) for p in ("solution/run_final.sh", "solution/cascade.conf", "run_v6c.sh"))
    check("run_final.sh + cascade.conf exist", os.path.exists(rf) and os.path.exists(cc))
    if os.path.exists(cc) and os.path.exists(v6):
        kv = conf(cc)
        passes, env = v6b_chain(v6)
        want = [p for p in passes]
        i = want.index("exc_fix") + 1
        want.insert(i, "exc_overpay")  # between exc_fix and cancel_guard
        got = kv.get("PASSES", "").split(",")
        check("cascade.conf PASSES == run_v6c.sh + exc_overpay", got == want, "" if got == want else f"got {got} want {want}")
        got_env = {k: v for k, v in kv.items() if k != "PASSES"}
        check("cascade.conf env == run_v6c.sh env", got_env == env, f"{got_env} vs {env}")
        s = open(rf, encoding="utf-8").read()
        check("run_final.sh reads cascade.conf and pins PYTHONHASHSEED=0", "cascade.conf" in s and "PYTHONHASHSEED=0" in s)
    else:
        check("run_v6c.sh available for the chain comparison", False, v6)
    # 3. masters_final
    import guards
    fin = os.path.join(a.data, "final", "masters_final")
    if a.phase == "final":
        try:
            guards.check_masters_final(a.data, "final")
            check("masters_final tables/columns match masters/", True)
        except SystemExit as e:
            check("masters_final tables/columns match masters/", False, str(e))
    # 4-5. tools / env
    check("pdftotext on PATH", shutil.which("pdftotext") is not None, shutil.which("pdftotext") or "")
    check("PYTHONHASHSEED pinned to 0 in this shell", os.environ.get("PYTHONHASHSEED") == "0",
          f"PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED')!r} (run_final.sh exports 0 itself)")
    # 6. ledger
    if not a.skip_ledger:
        import pandas as pd
        from data import Ctx
        ctx = Ctx(a.data, a.phase)
        p = os.path.join(a.data, a.phase, "open_items_at_start.csv.gz")
        oi = pd.read_csv(p, dtype={"item_id": str})
        oa = dict(zip(oi.item_id, oi.open_amount.astype(float)))
        missing = [i for i in oa if i not in ctx.items.index]
        wrong = [i for i, v in oa.items() if i in ctx.open and abs(ctx.open[i] - v) > 0.011]
        notopen = [i for i, v in oa.items() if v > 0.005 and i not in ctx.open]
        check("open_items_at_start fully loaded", not (missing or wrong or notopen),
              f"{len(oa)} rows; missing item {len(missing)}, open amount differs {len(wrong)}, not open {len(notopen)}; ledger {len(ctx.open)} open of {len(ctx.items)} items")
        bank_ids = set(ctx.bank.bank_txn_id)
        ent = dict(zip(ctx.bank.bank_txn_id, ctx.bank.entity))
        n_ent = Counter(ent.values())
    # 7. submission
    if a.sub:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "src", "validate_submission.py"), a.sub, "--phase", a.phase,
                            "--data", a.data], capture_output=True, text=True)
        print(r.stdout.strip()[-600:])
        check("validate_submission.py", r.returncode == 0 and "VALID" in r.stdout and "INVALID" not in r.stdout)
        import pandas as pd
        b = pd.read_csv(os.path.join(a.data, a.phase, "bank_transactions.csv.gz"), usecols=["bank_txn_id", "entity", "amount"])
        sub = pd.read_csv(a.sub, dtype=str, keep_default_na=False)
        dup = int(sub.duplicated(["bank_txn_id", "item_id"]).sum()) if "item_id" in sub else -1
        check("bank lines covered / no duplicate pairs", set(b.bank_txn_id) <= set(sub.bank_txn_id) and dup == 0,
              f"{sub.bank_txn_id.nunique()} of {len(b)} lines; duplicate pairs {dup}")
        u = set(sub[sub.exception == "UNIDENTIFIED"].bank_txn_id)
        b["unid"] = b.bank_txn_id.isin(u)
        print("UNIDENTIFIED share per entity (receipts only):")
        for e, g in b[b.amount > 0].groupby("entity"):
            print(f"   {e:8s} {g.unid.mean()*100:5.1f} %  ({int(g.unid.sum())} of {len(g)})")
    bad = RES.count(False)
    print(f"\n{'ALL CHECKS PASSED' if not bad else str(bad) + ' CHECK(S) FAILED'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
