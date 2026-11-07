"""Headroom of a dev run: how many score points each segment / exception class is worth, and what kind of errors it has.

    python solution/tools/headroom.py v35 [--data ~/hackdata/data_dev] [--top 10]     # writes notes/headroom_v35.md

Reads dev_runs/<run>/{sub.csv.gz, result.json, why.csv (optional), classes.csv} and the dev golden (with its segment column).
Diagnostic only: it never touches the pipeline and the golden/segment are never an input of any rule.

How the points are obtained (exact, no approximation): the run's submission is rebuilt in memory with the rows of a segment (or
class) replaced by the golden, and re-scored with the OFFICIAL scorer (starter/bankrec_score.py). points = new score - run score.
The tool also checks that (a) the unmodified submission reproduces result.json's score and (b) replacing every segment gives 100.
"""
import argparse
import bisect
import csv
import gzip
import json
import os
import re
import sys
from collections import Counter, defaultdict

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dev  # noqa: E402  (scorer import, EXC, read_sub, txn_ok)

S = dev.S
REQUIRED = S.REQUIRED
ERR_TYPES = [("a", "pares equivocados"), ("b", "pares bien, excepción mal"), ("c", "pares bien, importe mal"),
             ("d", "categoría/excepción mal (sin partida)"), ("e", "filas de más o de menos")]
TARGET_F1 = 0.9


# ------------------------------------------------------------------ rows / rescoring
def read_raw(path):
    """txn -> {item_id or '' : (amount, category, exception)} exactly as submitted"""
    op = gzip.open if path.endswith(".gz") else open
    rows = defaultdict(dict)
    with op(path, "rt", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            rows[r["bank_txn_id"]][r["item_id"]] = (r["amount"], r["category"], r["exception"])
    return rows


def golden_rows(g):
    """golden record of a txn in the same shape as read_raw"""
    items, cat, exc, _ = g
    if items:
        return {i: (f"{a:.2f}", "", e) for i, (a, e) in items.items()}
    return {"": ("", cat, exc)}


def serialize(rows):
    out = [",".join(REQUIRED)]
    for t, d in rows.items():
        for i, (a, c, e) in d.items():
            out.append(",".join((t, i, a, c, e)))
    return ("\n".join(out) + "\n").encode("utf-8")


def rescore(golden, rows):
    res = S.score_submission(golden, serialize(rows), "x.csv")
    if not res["ok"]:
        sys.exit("official scorer rejected a re-built submission: " + json.dumps(res["errors"][:3]))
    return res["score"]


def fix_txns(base, golden, txns):
    rows = dict(base)
    for t in txns:
        rows[t] = golden_rows(golden.txns[t])
    return rows


def fix_class_rows(base, golden, cls):
    """Correct only the rows of exception class `cls`: drop the submitted rows of that class, add the golden rows of that class.
    If that would mix item rows with a no-item row on the same line, the whole line is taken from the golden instead."""
    rows = dict(base)
    touched = 0
    for t, g in golden.txns.items():
        gr = golden_rows(g)
        gc = {i: v for i, v in gr.items() if v[2] == cls}
        cur = base.get(t, {})
        cc = {i for i, v in cur.items() if v[2] == cls}
        if not gc and not cc:
            continue
        new = {i: v for i, v in cur.items() if i not in cc}
        new.update(gc)
        if ("" in new and len(new) > 1):
            new = gr
        rows[t] = new
        touched += 1
    return rows, touched


# ------------------------------------------------------------------ error taxonomy
def error_type(g, p):
    gitems, gcat, gexc, _ = g
    if not gitems:
        return "d"
    if p is None or p["cat"] is not None or not p["items"]:
        return "a"
    ps, gs = set(p["items"]), set(gitems)
    if ps != gs:
        return "e" if (ps < gs or ps > gs) else "a"
    if any(p["items"][i][1] != gitems[i][1] for i in gs):
        return "b"
    return "c"


# ------------------------------------------------------------------ recall vs ambiguity (same method as bolsa.py)
def nrm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


class Replay:
    def __init__(self, D, golden, bank):
        self.golden, self.bank = golden, bank
        op = pd.read_csv(os.path.join(D, "live", "open_items_at_start.csv.gz"), dtype=str, keep_default_na=False)
        cols = ["item_id", "party_id", "document_number", "issue_date", "currency", "total_amount", "open_amount"]
        newi = []
        for fn, pc, dc in (("ar_invoices.csv.gz", "customer_id", "document_number"),
                           ("ap_invoices.csv.gz", "supplier_id", "supplier_invoice_number"),
                           ("credit_notes.csv.gz", "party_id", "document_number")):
            d = pd.read_csv(os.path.join(D, "live", fn), dtype=str, keep_default_na=False)
            newi.append(pd.DataFrame({"item_id": d.item_id, "party_id": d[pc], "document_number": d[dc], "issue_date": d.issue_date,
                                      "currency": d.currency, "total_amount": d.total_amount, "open_amount": d.total_amount}))
        items = pd.concat([op[cols]] + newi, ignore_index=True).drop_duplicates("item_id").set_index("item_id")
        items["open0"] = items.open_amount.astype(float)
        self.items = items
        self.by_party = defaultdict(list)
        for iid, pid in zip(items.index, items.party_id):
            self.by_party[pid].append(iid)
        bdate = bank.booking_date.to_dict()
        self.bdate = bdate
        ev = defaultdict(list)
        for t, g in golden.txns.items():
            for iid, (amt, exc) in g[0].items():
                delta = amt if exc == "RETURNED" else (0.0 if exc == "DUPLICATE_PAYMENT" else -amt)
                ev[iid].append((bdate[t], t, delta))
        for iid in ev:
            ev[iid].sort()
        self.ev = ev
        self.evkeys = {iid: [(d, t) for d, t, _ in v] for iid, v in ev.items()}

    def open_at(self, iid, t):
        o = self.items.at[iid, "open0"]
        k = bisect.bisect_left(self.evkeys.get(iid, []), (self.bdate[t], t))
        return o + sum(x[2] for x in self.ev.get(iid, [])[:k])

    def classify(self, t):
        """'recall' | 'ambiguity' | 'not findable'"""
        gi = self.golden.txns[t][0]
        ok_items = [iid for iid in gi if iid in self.items.index]
        if not ok_items:
            return "not findable"
        party = Counter(self.items.at[iid, "party_id"] for iid in ok_items).most_common(1)[0][0]
        same_cur = self.bank.at[t, "currency"] == self.items.at[ok_items[0], "currency"]
        amt = abs(float(self.bank.at[t, "amount"]))
        cand, rng = set(), 0
        for iid in self.by_party[party]:
            if self.items.at[iid, "issue_date"] > self.bdate[t]:
                continue
            o = self.open_at(iid, t)
            if o > 0.005:
                cand.add(iid)
                if (not same_cur) or o <= amt * 1.01:
                    rng += 1
        if sum(1 for iid in gi if iid in cand) < len(gi):
            return "not findable"
        text = nrm(" ".join(str(self.bank.at[t, c]) for c in ("text", "ref1", "ref2", "remittance_info", "end_to_end_id")
                            if c in self.bank.columns))
        digits = re.sub(r"\D", "", text)
        refs = 0
        for iid in gi:
            dn = nrm(self.items.at[iid, "document_number"])
            dg = re.sub(r"\D", "", dn)
            if (len(dn) >= 5 and dn in text) or (len(dg) >= 5 and dg[-6:] in digits):
                refs += 1
        return "recall" if (refs > 0 or rng <= 5) else "ambiguity"


# ------------------------------------------------------------------ markdown helpers
def md_table(headers, rows, align=None):
    align = align or ["l"] + ["r"] * (len(headers) - 1)
    L = ["| " + " | ".join(headers) + " |", "|" + "|".join("---:" if a == "r" else "---" for a in align) + "|"]
    for r in rows:
        L.append("| " + " | ".join(str(c) for c in r) + " |")
    return L


def pts(x):
    return f"{x:.2f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--data", default=None, help="dev dataset dir (default: the one recorded in result.json)")
    ap.add_argument("--top", type=int, default=10, help="segments in the detailed sections (default 10)")
    ap.add_argument("--out", default=None, help="output file (default notes/headroom_<run>.md)")
    a = ap.parse_args()

    run_dir = os.path.join(dev.ROOT, "dev_runs", a.run)
    res_json = json.load(open(os.path.join(run_dir, "result.json")))
    D = os.path.expanduser(a.data or res_json["meta"]["dataset"])
    base_score = res_json["score"]
    bt = os.path.join(D, "live", "bank_transactions.csv.gz")
    cats = [r["category"] for r in csv.DictReader(open(os.path.join(D, "masters", "categories.csv"), encoding="utf-8"))]
    golden = S.load_golden(os.path.join(D, "golden_dev.csv"), bt, dev.EXC, cats)
    sub_path = os.path.join(run_dir, "sub.csv.gz")
    base = read_raw(sub_path)
    pred = dev.read_sub(sub_path)
    why = {}
    wp = os.path.join(run_dir, "why.csv")
    if os.path.exists(wp):
        with open(wp, newline="", encoding="utf-8") as fh:
            why = {r["bank_txn_id"]: r["why"] for r in csv.DictReader(fh)}
    bank = pd.read_csv(bt, dtype=str, keep_default_na=False).set_index("bank_txn_id")

    # ---- self-checks
    score0 = rescore(golden, base)
    assert abs(score0 - base_score) < 0.006, f"rebuilt submission scores {score0}, result.json says {base_score}"
    seg_txns = defaultdict(list)
    for t, g in golden.txns.items():
        seg_txns[g[3]].append(t)
    wrong = {t for t, g in golden.txns.items() if not dev.txn_ok(g, pred.get(t))}
    oracle = rescore(golden, fix_txns(base, golden, golden.txns))
    assert abs(oracle - 100) < 1e-9, f"oracle scores {oracle}"
    N = len(golden.txns)
    gap = 100 - score0

    # ---- 1. segments
    seg_rows = []
    for seg, ts in seg_txns.items():
        w = [t for t in ts if t in wrong]
        p = rescore(golden, fix_txns(base, golden, w)) - score0 if w else 0.0
        seg_rows.append({"seg": seg, "n": len(ts), "wrong": len(w), "acc": 1 - len(w) / len(ts), "pts": p, "wrong_txns": w})
    seg_rows.sort(key=lambda r: (-r["pts"], -r["wrong"]))
    total_seg = sum(r["pts"] for r in seg_rows)

    # ---- 2. classes
    cls_csv = {r["class"]: r for r in csv.DictReader(open(os.path.join(run_dir, "classes.csv"), encoding="utf-8"))}
    k_cls = sum(1 for r in cls_csv.values() if int(r["golden_rows"]) > 0)
    cls_rows = []
    for c in dev.EXC:
        r = cls_csv[c]
        f1, sup = float(r["f1"]), int(r["golden_rows"])
        rows_c, touched = fix_class_rows(base, golden, c)
        p_rows = rescore(golden, rows_c) - score0
        tl = [t for t, g in golden.txns.items()
              if any(e == c for _, e in g[0].values()) or (not g[0] and g[2] == c)
              or any(v[2] == c for v in base.get(t, {}).values())]
        p_lines = rescore(golden, fix_txns(base, golden, tl)) - score0
        p09 = 100 * S.W_EXC * max(0.0, TARGET_F1 - f1) / k_cls if sup else 0.0
        cls_rows.append({"cls": c, "f1": f1, "sup": sup, "pred": int(r["pred_rows"]), "p_rows": p_rows, "p_lines": p_lines,
                         "p09": p09, "small": sup < dev.SMALL_N})
    cls_rows.sort(key=lambda r: -r["p_rows"])

    # ---- 3. taxonomy
    tax = {}
    etype = {}
    for t in wrong:
        e = error_type(golden.txns[t], pred.get(t))
        etype[t] = e
        tax.setdefault(golden.txns[t][3], Counter())[e] += 1

    top = [r for r in seg_rows if r["wrong"]][:a.top]
    replay = Replay(D, golden, bank) if top else None
    detail = {}
    for r in top:
        seg = r["seg"]
        w = sorted(r["wrong_txns"])
        kinds = Counter()
        kind_of = {}
        for t in w:
            if golden.txns[t][0]:
                kind_of[t] = replay.classify(t)
                kinds[kind_of[t]] += 1
            else:
                kind_of[t] = "sin partida"
                kinds["sin partida"] += 1
        # matcher
        lab_n, lab_w = Counter(), Counter()
        for t in seg_txns[seg]:
            lab = why.get(t, "") or "(sin informe)"
            lab_n[lab] += 1
            if t in wrong:
                lab_w[lab] += 1
        # 5 examples: round-robin over error types (most frequent first), ids in order
        by_type = defaultdict(list)
        for t in w:
            by_type[etype[t]].append(t)
        order = [k for k, _ in Counter({k: len(v) for k, v in by_type.items()}).most_common()]
        ex, i = [], 0
        while len(ex) < min(5, len(w)):
            for k in order:
                if i < len(by_type[k]) and len(ex) < 5:
                    ex.append(by_type[k][i])
            i += 1
        detail[seg] = {"kinds": kinds, "lab_n": lab_n, "lab_w": lab_w, "examples": ex, "kind_of": kind_of}

    # ---- difficulty heuristic + next experiment
    def difficulty(seg):
        d = detail[seg]
        tc = tax[seg]
        n = sum(tc.values())
        findable = d["kinds"]["recall"] + d["kinds"]["ambiguity"] + d["kinds"]["not findable"]
        amb = (d["kinds"]["ambiguity"] + d["kinds"]["not findable"]) / n
        rec = d["kinds"]["recall"] / n
        rule = (tc["b"] + tc["c"] + tc["d"]) / n
        if rule >= 0.5 or rec >= 0.6:
            return "baja", 1.0
        if amb >= 0.6:
            return "alta", 0.25
        return "media", 0.5

    def sentence(seg):
        d, tc = detail[seg], tax[seg]
        n = sum(tc.values())
        dom, dn = tc.most_common(1)[0]
        dom_name = dict(ERR_TYPES)[dom]
        main_lab = d["lab_w"].most_common(1)[0]
        k = d["kinds"]
        if dom == "a" and k["not findable"] > max(k["recall"], k["ambiguity"]):
            how = ("la mayoría son partidas que no estaban abiertas en ese momento (duplicados, devoluciones, consumidas antes): "
                   "hace falta modelar el estado del ledger, no más cobertura")
        elif dom == "a" and k["ambiguity"] > k["recall"]:
            how = (f"la mayoría es ambigüedad (varios candidatos y sin referencia): hace falta un criterio de selección entre "
                   f"candidatos (importes, fechas, FIFO), no más cobertura")
        elif dom == "a":
            how = "la mayoría es recall (referencia visible o pocos candidatos): ampliar la cobertura del matcher de referencias"
        elif dom == "b":
            how = "los pares son correctos: basta una regla para la excepción de esas filas"
        elif dom == "c":
            how = "los pares son correctos: revisar el cálculo del importe asignado a cada partida"
        elif dom == "d":
            how = "no hay partida: mejorar la regla de categoría/excepción del movimiento"
        else:
            how = "los pares emitidos son correctos pero sobran o faltan filas: ajustar el criterio de parada del emparejamiento"
        return f"{pct(dn, n)} de sus fallos son «{dom_name}» (matcher principal `{main_lab[0]}`, {main_lab[1]} fallos); {how}."

    def pct(x, n):
        return f"{100 * x / n:.0f} %"

    # ------------------------------------------------------------------ write
    L = [f"# Headroom of run `{a.run}`", "",
         f"- Score del run: **{score0:.2f}** (result.json: {base_score:.2f}) → faltan **{gap:.2f}** puntos hasta 100.",
         f"- Ventana: {res_json['meta'].get('window')}, {N} movimientos, dataset `{D}`.",
         "- Método: se reconstruye el envío del run con las filas de un segmento (o clase) sustituidas por el golden y se repuntúa "
         "con el scorer oficial (`starter/bankrec_score.py`); puntos = score nuevo − score del run. Cifras exactas, no aproximadas.",
         f"- Comprobaciones hechas por la herramienta: el envío sin modificar reproduce {base_score:.2f}; sustituir todos los segmentos "
         f"da {oracle:.2f}.", ""]

    L += ["## 1. Margen por segmento", "",
          "Puntos que sube el score si los movimientos del segmento pasan a ser exactamente el golden (segmentos sin fallos: 0,00, omitidos).", ""]
    L += md_table(["segmento", "movimientos", "accuracy actual", "mal", "puntos", "% del gap"],
                  [[r["seg"], r["n"], f"{r['acc']:.3f}", r["wrong"], pts(r["pts"]), pct(r["pts"], gap)]
                   for r in seg_rows if r["wrong"]])
    resid = gap - total_seg
    L += ["", f"**Coherencia**: suma de márgenes por segmento = **{total_seg:.2f}** frente a **{gap:.2f}** que faltan "
          f"(100 − {score0:.2f}); diferencia {resid:+.2f}. No coinciden exactamente porque el score no es lineal "
          f"(F1 de asignación y macro-F1 de excepciones son cocientes): cada segmento se mide aislado, con el resto tal como está, "
          f"mientras que corregirlos todos a la vez da exactamente 100. Los movimientos mal suman {len(wrong)} "
          f"({100 * len(wrong) / N:.2f} % de {N}), que por sí solo vale {100 * S.W_TXN * len(wrong) / N:.2f} puntos del término de accuracy (peso 0,3).", ""]

    L += ["## 2. Margen por clase de excepción", "",
          "«Filas»: se sustituyen solo las filas de esa clase (las enviadas y las del golden) y el resto del envío queda igual. "
          "«Líneas»: se sustituyen por el golden todos los movimientos donde esa clase aparece en el envío o en el golden "
          "(incluye el efecto sobre accuracy del movimiento). «A F1 0,9»: puntos del término macro-F1 si la clase llegara a F1 0,9 "
          f"(= 100·0,2·(0,9 − F1)/{k_cls} clases con soporte; 0 si ya la supera).", ""]
    L += md_table(["clase", "F1 actual", "soporte", "filas enviadas", "puntos (filas)", "puntos (líneas)", "puntos a F1 0,9", "nota"],
                  [[r["cls"], f"{r['f1']:.3f}", r["sup"], r["pred"], pts(r["p_rows"]), pts(r["p_lines"]), pts(r["p09"]),
                    "(n pequeño)" if r["small"] else ""] for r in cls_rows], align=["l"] + ["r"] * 6 + ["l"])
    L += [""]

    L += ["## 3. Taxonomía de los movimientos mal, por segmento", "",
          "Cada movimiento mal cae en un único tipo, por este orden: (d) el golden no tiene partidas y la respuesta difiere; "
          "(a) se respondió sin partidas o con un conjunto de partidas que no contiene ni está contenido en el golden; "
          "(e) el conjunto enviado es subconjunto/superconjunto estricto del golden; con el mismo conjunto: (b) alguna excepción difiere, "
          "si no (c) algún importe difiere más allá de la tolerancia.", ""]
    L += md_table(["segmento", "mal", "(a) pares", "(b) excepción", "(c) importe", "(d) categoría", "(e) filas +/−"],
                  [[r["seg"], r["wrong"]] + [tax[r["seg"]][k] for k in "abcde"] for r in seg_rows if r["wrong"]]
                  + [["**total**", len(wrong)] + [sum(c[k] for c in tax.values()) for k in "abcde"]])
    L += [""]

    L += [f"## 4. Detalle del top {len(top)} por puntos", "",
          "Los ejemplos son bank_txn_id mal, uno de cada tipo de error por turnos, para `inspect_txn.py` "
          "(esa herramienta no existe aún en este checkout). Recall = el golden estaba abierto y había referencia visible o ≤ 5 "
          "candidatos en rango; ambigüedad = abierto, sin referencia y > 5 candidatos; no localizable = alguna partida no estaba abierta "
          "en ese momento (mismo método que `notes/bolsa_*.md`, ver `solution/tools/bolsa.py`).", ""]
    for r in top:
        seg, d = r["seg"], detail[r["seg"]]
        L += [f"### {seg} — {pts(r['pts'])} pts, {r['wrong']} mal de {r['n']}", ""]
        tc = tax[seg]
        nofound = sum(1 for t in r["wrong_txns"] if etype[t] == "a" and (pred.get(t) is None or not pred[t]["items"]))
        L.append("- Tipos de error: " + ", ".join(f"({k}) {tc[k]}" for k in "abcde" if tc[k])
                 + (f". De los (a), {nofound} se respondieron sin ninguna partida (no encontrado) y {tc['a'] - nofound} con otras partidas" if tc["a"] else "") + ".")
        k = d["kinds"]
        L.append("- Fallo de: " + ", ".join(f"{name} {k[key]}" for key, name in
                                            (("recall", "recall"), ("ambiguity", "ambigüedad"), ("not findable", "no localizable"),
                                             ("sin partida", "sin partida (categoría)")) if k[key]) + ".")
        if why:
            L.append("- Pase que los resuelve (fallos / movimientos del segmento por pase): " + "; ".join(
                f"`{lab}` {d['lab_w'][lab]}/{n}" for lab, n in sorted(d["lab_n"].items(), key=lambda x: -d["lab_w"][x[0]])[:5]
                if d["lab_w"][lab]) + ".")
        else:
            L.append("- Pase: sin informe de pases (`why.csv`) en este run.")
        L.append("- Ejemplos: " + ", ".join(f"`{t}` ({etype[t]}, {d['kind_of'][t]}, {why.get(t, '-') or '-'})" for t in d["examples"]))
        L.append("")

    L += ["## 5. Siguiente experimento", "",
          "Dificultad estimada (heurística, no medida): **baja** si ≥ 50 % de los fallos son de excepción/importe/categoría (b/c/d) o ≥ 60 % "
          "son recall; **alta** si ≥ 60 % son ambigüedad o no localizables; **media** el resto. Prioridad = puntos × (1 / 0,5 / 0,25 "
          "para baja / media / alta).", ""]
    nx = []
    for r in top:
        dif, f = difficulty(r["seg"])
        nx.append((r["pts"] * f, r, dif))
    nx.sort(key=lambda x: -x[0])
    L += md_table(["#", "segmento", "puntos", "tipo dominante", "recall / ambigüedad", "dificultad", "prioridad", "qué probar"],
                  [[i + 1, r["seg"], pts(r["pts"]), dict(ERR_TYPES)[tax[r["seg"]].most_common(1)[0][0]],
                    f"{detail[r['seg']]['kinds']['recall']} / {detail[r['seg']]['kinds']['ambiguity'] + detail[r['seg']]['kinds']['not findable']}",
                    dif, f"{pr:.2f}", sentence(r["seg"])] for i, (pr, r, dif) in enumerate(nx)],
                  align=["r", "l", "r", "l", "r", "l", "r", "l"])
    L += [""]

    out = a.out or os.path.join(dev.ROOT, "notes", f"headroom_{a.run}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"[headroom] {a.run}: score {score0:.2f}, gap {gap:.2f}, sum of segment margins {total_seg:.2f} -> {out}")


if __name__ == "__main__":
    main()
