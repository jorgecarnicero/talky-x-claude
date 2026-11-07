"""Import the team's v3 answers (solution/bankrec.py on main/model/v3) as a pass.

Set V3_SUB=<path to v3 csv(.gz) for the same phase>. Two entry points:
  run(ctx)          : v3 lines WITH items, for lines not yet claimed by an earlier (higher-precision) pass.
                      Items already cleared in our ledger by an earlier pass are dropped from the v3 line; if that
                      empties the line it is left for later passes.
  run_rest(ctx)     : (pass 'v3rest') v3's answer for every still-unclaimed line (categories / UNIDENTIFIED).
"""
import csv
import gzip
import os


def _load(ctx):
    if "v3" in ctx.cache:
        return ctx.cache["v3"]
    path = os.environ.get("V3_SUB")
    if path and os.environ.get("V3_WAIT") == "1":
        # parallel chain (run_v6c.sh): the backbone runs concurrently; wait for its "<file>.done" marker
        import time
        t0 = time.time()
        while not os.path.exists(path + ".done"):
            if time.time() - t0 > float(os.environ.get("V3_WAIT_MAX", "3600")):
                raise SystemExit(f"v3import: timed out waiting for {path}.done")
            time.sleep(5)
        print(f"    v3import: backbone ready after waiting {time.time() - t0:.0f}s", flush=True)
    if not path or not os.path.exists(path):
        raise SystemExit("v3import: set V3_SUB to the v3 submission for this phase")
    op = gzip.open if path.endswith(".gz") else open
    v3 = {}
    with op(path, "rt", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            v3.setdefault(r["bank_txn_id"], []).append(r)
    ctx.cache["v3"] = v3
    return v3


def run(ctx):
    v3 = _load(ctx)
    ctx.cache["cleared_by_us"] = {r["item_id"] for rows in ctx.results.values() for r in rows if r["item_id"]}
    for t in ctx.bank.bank_txn_id:
        if ctx.done(t):
            continue
        rows = v3.get(t)
        if not rows or not rows[0]["item_id"]:
            continue
        keep = []
        for r in rows:
            iid, exc = r["item_id"], r["exception"]
            # an item already fully cleared by an earlier pass can only be re-hit as duplicate/returned
            if exc not in ("DUPLICATE_PAYMENT", "RETURNED") and iid not in ctx.open \
                    and iid in ctx.cache["cleared_by_us"]:
                continue
            keep.append({"item_id": iid, "amount": float(r["amount"]), "exception": exc})
        if keep:
            ctx.allocate(t, keep, "v3")


def run_rest(ctx):
    v3 = _load(ctx)
    for t in ctx.bank.bank_txn_id:
        if ctx.done(t) or t not in v3:
            continue
        rows = v3[t]
        if rows[0]["item_id"]:
            ctx.allocate(t, [{"item_id": r["item_id"], "amount": float(r["amount"]), "exception": r["exception"]}
                             for r in rows], "v3rest")
        else:
            ctx.allocate(t, [{"category": rows[0]["category"], "exception": rows[0]["exception"]}], "v3rest")
