"""Paired significance of a candidate against the trunk on dev: gates G1, G2, G3 of notes/ANTI_OVERFIT.md.

    python tools/significance.py TRUNK_SUB CANDIDATE_SUB [--data data_dev] [--json out.json]

    TRUNK_SUB / CANDIDATE_SUB : dev submissions (sub.csv.gz of two dev_runs/<name>/ of the SAME window)
    --data                    : dev dataset dir; reads golden_dev.csv, live/bank_transactions.csv.gz and the item
                                files of live/ (only to know the party of each golden item). Read-only.

Unit = one bank movement, correct or not by the same rule as dev.py (exact item set, amounts within the official
tolerance, exceptions equal; or category + exception for lines without items).

  G1  McNemar on discordant movements: c = wrong->right, b = right->wrong, z = (c-b)/sqrt(b+c).
      Pass: z >= 3 and c-b >= 30.
  G2  Sign test per payer (movements of one payer are not independent). Payer = party of the golden items of the
      movement, else entity + normalized counterparty name, else the movement itself. Per payer n = c_p - b_p;
      P+ = payers with n > 0, P- = payers with n < 0. Pass: exact one-sided binomial p(P+ | P+ + P-, 1/2) <= 0.01
      and no single payer carries more than 25 % of c-b.
  G3  Balance c-b > 0 in every month of the window, and >= 0 in every entity with more than 1,000 movements.

Verdict: ACEPTAR if G1, G2 and G3 pass. RECHAZAR if the candidate is significantly worse overall (z <= -2), or
significantly worse in one month / big entity (slice z <= -2 and c-b <= -10), or the payer sign test is
significantly negative (p(P- ...) <= 0.01). NEUTRO otherwise (no evidence either way: do not merge, do not spend a
LIVE upload). Exit code 0 / 1 / 2 = ACEPTAR / NEUTRO / RECHAZAR.

Only reads files; never runs the pipeline. G1-G3 measure movement accuracy, not the composite official score.
"""
import argparse
import csv
import gzip
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
DATA = os.environ.get("BANKREC_DATA", os.path.join(ROOT, "data"))
sys.path.insert(0, os.path.join(DATA, "starter"))  # official scorer ships with the data archive
from bankrec_score import _tol  # noqa: E402  (official amount tolerance)

Z_MIN, NET_MIN = 3.0, 30           # G1
P_PAYER, TOP_SHARE = 0.01, 0.25    # G2
BIG_ENTITY = 1000                  # G3
Z_HARM, NET_HARM = -2.0, -10       # RECHAZAR


def _open(path):
    return gzip.open(path, "rt", encoding="utf-8-sig", newline="") if path.endswith(".gz") else \
        open(path, encoding="utf-8-sig", newline="")


def read_sub(path):
    pred = {}
    with _open(path) as fh:
        for r in csv.DictReader(fh):
            p = pred.setdefault(r["bank_txn_id"], {"items": {}, "cat": None, "exc": None})
            if r["item_id"]:
                p["items"][r["item_id"]] = (float(r["amount"]), (r["exception"] or "").upper())
            else:
                p["cat"], p["exc"] = (r["category"] or "").upper(), (r["exception"] or "").upper()
    return pred


def read_golden(path):
    g = {}
    with _open(path) as fh:
        for r in csv.DictReader(fh):
            rec = g.setdefault(r["bank_txn_id"], [{}, "", ""])
            if r["item_id"]:
                rec[0][r["item_id"]] = (float(r["amount"]), r["exception"])
            else:
                rec[1], rec[2] = r["category"], r["exception"]
    return g


def txn_ok(g, p):
    """same rule as dev.py txn_ok"""
    gitems, gcat, gexc = g
    if p is None:
        return False
    if gitems:
        if p["cat"] is not None or len(p["items"]) != len(gitems):
            return False
        return all(i in gitems and abs(a - gitems[i][0]) <= _tol(gitems[i][0]) and e == gitems[i][1]
                   for i, (a, e) in p["items"].items())
    return p["cat"] is not None and not p["items"] and p["cat"] == gcat and p["exc"] == gexc


def item_parties(live):
    party = {}
    for name, col in (("open_items_at_start", "party_id"), ("ar_invoices", "customer_id"),
                      ("ap_invoices", "supplier_id"), ("credit_notes", "party_id")):
        path = os.path.join(live, name + ".csv.gz")
        if not os.path.exists(path):
            continue
        with _open(path) as fh:
            for r in csv.DictReader(fh):
                if r.get(col):
                    party.setdefault(r["item_id"], r[col])
    return party


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z ]", " ", s).split())


def binom_upper(k, n):
    """P(X >= k), X ~ Bin(n, 1/2), exact (log space)"""
    if n == 0:
        return 1.0
    lt = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) - n * math.log(2) for i in range(k, n + 1)]
    m = max(lt)
    return min(1.0, math.exp(m) * sum(math.exp(x - m) for x in lt))


def mcnemar(c, b):
    n = b + c
    return {"c": c, "b": b, "net": c - b, "z": (c - b) / math.sqrt(n) if n else 0.0,
            "p_better": binom_upper(c, n), "p_worse": binom_upper(b, n)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("trunk")
    ap.add_argument("candidate")
    ap.add_argument("--data", default="data_dev")
    ap.add_argument("--json")
    a = ap.parse_args()
    data = os.path.abspath(os.path.expanduser(a.data))
    live = os.path.join(data, "live")

    golden = read_golden(os.path.join(data, "golden_dev.csv"))
    base, cand = read_sub(a.trunk), read_sub(a.candidate)
    party = item_parties(live)
    meta = {}
    with _open(os.path.join(live, "bank_transactions.csv.gz")) as fh:
        for r in csv.DictReader(fh):
            meta[r["bank_txn_id"]] = (r["entity"], r["booking_date"][:7], r["counterparty_name"])
    missing = [t for t in golden if t not in meta]
    if missing:
        sys.exit(f"{len(missing)} golden movements not in {live}/bank_transactions.csv.gz (wrong --data?)")

    n = len(golden)
    acc_b = acc_c = 0
    B, C = Counter(), Counter()          # per slice key
    pay_b, pay_c = Counter(), Counter()  # per payer
    ent_n = Counter()
    for t, g in golden.items():
        ent, month, cp = meta[t]
        ent_n[ent] += 1
        ob, oc = txn_ok(g, base.get(t)), txn_ok(g, cand.get(t))
        acc_b += ob
        acc_c += oc
        if ob == oc:
            continue
        ps = Counter(party.get(i) for i in g[0] if party.get(i))
        payer = ps.most_common(1)[0][0] if ps else (f"{ent}:{norm_name(cp)}" if norm_name(cp) else t)
        tgt, ptgt = (C, pay_c) if oc else (B, pay_b)
        for k in ("all", "m:" + month, "e:" + ent):
            tgt[k] += 1
        ptgt[payer] += 1

    g1 = mcnemar(C["all"], B["all"])
    g1["pass"] = g1["z"] >= Z_MIN and g1["net"] >= NET_MIN

    nets = {p: pay_c[p] - pay_b[p] for p in set(pay_b) | set(pay_c)}
    pp, pm = sum(v > 0 for v in nets.values()), sum(v < 0 for v in nets.values())
    top = sorted(nets.items(), key=lambda kv: -abs(kv[1]))[:5]
    top_share = (max(nets.values()) / g1["net"]) if g1["net"] > 0 and nets else None
    g2 = {"payers_moved": len(nets), "P+": pp, "P-": pm, "ties": len(nets) - pp - pm,
          "z": (pp - pm) / math.sqrt(pp + pm) if pp + pm else 0.0,
          "p_better": binom_upper(pp, pp + pm), "p_worse": binom_upper(pm, pp + pm),
          "top_payer_share": top_share, "top": top}
    g2["pass"] = g2["p_better"] <= P_PAYER and top_share is not None and top_share <= TOP_SHARE

    months = sorted({m for _, m, _ in meta.values()})
    slices = []
    for m in months:
        s = mcnemar(C["m:" + m], B["m:" + m])
        s.update(slice="mes " + m, n=None, rule="> 0", ok=s["net"] > 0)
        slices.append(s)
    for e in sorted(ent_n):
        s = mcnemar(C["e:" + e], B["e:" + e])
        big = ent_n[e] > BIG_ENTITY
        s.update(slice="entidad " + e, n=ent_n[e], rule=">= 0" if big else "(< 1.000 mov., informativa)",
                 ok=(s["net"] >= 0) if big else True)
        slices.append(s)
    g3 = {"pass": all(s["ok"] for s in slices), "slices": slices}

    harm = [s["slice"] for s in slices if (s["n"] is None or s["n"] > BIG_ENTITY)
            and s["z"] <= Z_HARM and s["net"] <= NET_HARM]
    if g1["z"] <= Z_HARM or harm or g2["p_worse"] <= P_PAYER:
        verdict = "RECHAZAR"
    elif g1["pass"] and g2["pass"] and g3["pass"]:
        verdict = "ACEPTAR"
    else:
        verdict = "NEUTRO"

    ok = lambda f: "PASA" if f else "NO PASA"  # noqa: E731
    print(f"movimientos dev: {n}   acierto tronco {acc_b / n:.4%}  candidato {acc_c / n:.4%}  "
          f"(delta {acc_c - acc_b:+d} mov., {100 * (acc_c - acc_b) / n:+.3f} pt)")
    print(f"tronco:    {a.trunk}\ncandidato: {a.candidate}\n")
    print(f"G1 McNemar   c (mal->bien) = {g1['c']}   b (bien->mal) = {g1['b']}   c-b = {g1['net']:+d}   "
          f"z = {g1['z']:.2f}   p exacta unilateral = {g1['p_better']:.2g}")
    print(f"             umbral z >= {Z_MIN:g} y c-b >= {NET_MIN}  ->  {ok(g1['pass'])}")
    print(f"G2 pagadores pagadores movidos = {g2['payers_moved']}   P+ = {pp}   P- = {pm}   empates = {g2['ties']}   "
          f"z = {g2['z']:.2f}   p exacta unilateral = {g2['p_better']:.2g}")
    ts = f"{top_share:.0%}" if top_share is not None else "n/a"
    print(f"             mayor pagador = {ts} de c-b (máx {TOP_SHARE:.0%});  top: " +
          ", ".join(f"{p} {v:+d}" for p, v in top))
    print(f"             umbral p <= {P_PAYER:g} y mayor pagador <= {TOP_SHARE:.0%}  ->  {ok(g2['pass'])}")
    print("G3 saldo por mes y por entidad")
    for s in slices:
        nn = f" ({s['n']} mov.)" if s["n"] is not None else ""
        print(f"             {s['slice']:<16}{nn:<14} c = {s['c']:>4}  b = {s['b']:>4}  c-b = {s['net']:+5d}  "
              f"[{s['rule']}] {'ok' if s['ok'] else 'FALLA'}")
    print(f"             ->  {ok(g3['pass'])}")
    if harm:
        print(f"daño significativo en: {', '.join(harm)}")
    print(f"\nVEREDICTO: {verdict}")
    print("  (G4-G7 no se miden aquí: ventana sellada, barrido de literales, LIVE, variable de entorno; "
          "ver notes/ANTI_OVERFIT.md)")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"trunk": a.trunk, "candidate": a.candidate, "data": data, "n": n, "acc_trunk": acc_b / n,
                       "acc_candidate": acc_c / n, "G1": g1, "G2": g2, "G3": g3, "harm": harm,
                       "verdict": verdict}, fh, indent=1)
    sys.exit({"ACEPTAR": 0, "NEUTRO": 1, "RECHAZAR": 2}[verdict])


if __name__ == "__main__":
    main()
