"""Headroom of a dev run: how many score points each segment / exception class is worth, and what kind of errors it has.

    python solution/tools/headroom.py v35 [--data ~/hackdata/data_dev] [--top 10]     # writes notes/headroom_v35.md

Reads dev_runs/<run>/{sub.csv.gz, result.json, why.csv (optional), classes.csv} and the dev golden (with its segment column).
Diagnostic only: it never touches the pipeline and the golden/segment are never an input of any rule.

How the points are obtained (exact, no approximation): the run's submission is rebuilt in memory with the rows of a segment (or
class) replaced by the golden, and re-scored with the OFFICIAL scorer (starter/bankrec_score.py). points = new score - run score.
The tool also checks that (a) the unmodified submission reproduces result.json's score and (b) replacing every segment gives 100.
"""
import argparse
import csv
import gzip
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hr_lib  # noqa: E402

dev, S = hr_lib.dev, hr_lib.S
REQUIRED = S.REQUIRED
ERR_TYPES = [("a", "pares equivocados"), ("b", "pares bien, excepción mal"), ("c", "pares bien, importe mal"),
             ("d", "categoría/excepción mal (sin partida)"), ("e", "filas de más o de menos")]
ERR_NAME = dict(ERR_TYPES)
TARGET_F1 = 0.9
EASE = {"baja": 1.0, "media": 0.5, "alta": 0.25}


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
    for t, g in golden.txns.items():
        gr = golden_rows(g)
        gc = {i: v for i, v in gr.items() if v[2] == cls}
        cur = base.get(t, {})
        cc = {i for i, v in cur.items() if v[2] == cls}
        if not gc and not cc:
            continue
        new = {i: v for i, v in cur.items() if i not in cc}
        new.update(gc)
        if "" in new and len(new) > 1:
            new = gr
        rows[t] = new
    return rows


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


# ------------------------------------------------------------------ recoverability (payer = the pipeline's, not the golden's)
def recoverability(run, rp, pid, t):
    """-> (class, payer_how, payer_right) with class in recall | ambiguity | impossible | sin partida

    impossible : a golden item was not open at that moment (duplicate, already consumed / returned, not in the ledger);
    recall     : otherwise, a golden document number is visible in the text, or the open items of the payer the PIPELINE
                 identifies include all golden items and at most 5 of them are in range (open <= payment x1.01);
    ambiguity  : otherwise (no reference, more than 5 candidates, or the pipeline's payer is missing / not the golden's party)."""
    gi = run.golden.txns[t][0]
    if not gi:
        return "sin partida", None, None
    party, how = pid.identify(t)
    gparties = {rp.party_of(i) for i in gi}
    right = party is not None and party in gparties
    if any(i not in rp.items.index or rp.open_at(i, t) <= 0.005 for i in gi):
        return "impossible", how, right
    if rp.refs_visible(t, list(gi)) > 0:
        return "recall", how, right
    if right:
        cands = rp.candidates(party, t)
        ids = {c[0] for c in cands}
        if all(i in ids for i in gi) and sum(1 for c in cands if c[2]) <= 5:
            return "recall", how, right
    return "ambiguity", how, right


def difficulty(tax, kinds):
    n = sum(tax.values())
    rec = kinds.get("recall", 0) / n
    amb = (kinds.get("ambiguity", 0) + kinds.get("impossible", 0)) / n
    rule = (tax.get("b", 0) + tax.get("c", 0) + tax.get("d", 0)) / n
    if rule >= 0.5 or rec >= 0.6:
        return "baja"
    if amb >= 0.6:
        return "alta"
    return "media"


# ------------------------------------------------------------------ the computation
def compute(run_name, data=None, top=10, light=False, log=print):
    """Everything the report needs, as a JSON-serialisable dict. light=True: segment points only (no classes, no detail)."""
    run = hr_lib.Run(run_name, data)
    golden, pred, why = run.golden, run.pred, run.why
    base = read_raw(os.path.join(run.dir, "sub.csv.gz"))
    base_score = run.res["score"]
    score0 = rescore(golden, base)
    assert abs(score0 - base_score) < 0.006, f"rebuilt submission scores {score0}, result.json says {base_score}"
    N = len(golden.txns)
    wrong = {t for t in golden.txns if not run.ok(t)}
    seg_txns = defaultdict(list)
    for t, g in golden.txns.items():
        seg_txns[g[3]].append(t)
    oracle = None
    if not light:
        oracle = rescore(golden, fix_txns(base, golden, golden.txns))
        assert abs(oracle - 100) < 1e-9, f"oracle scores {oracle}"
    etype = {t: error_type(golden.txns[t], pred.get(t)) for t in wrong}

    segs = []
    for seg, ts in seg_txns.items():
        w = [t for t in ts if t in wrong]
        pts = rescore(golden, fix_txns(base, golden, w)) - score0 if w else 0.0
        tax = Counter(etype[t] for t in w)
        segs.append({"segment": seg, "n": len(ts), "wrong": len(w), "accuracy": 1 - len(w) / len(ts), "points": pts,
                     "tax": {k: tax.get(k, 0) for k in "abcde"}})
    segs.sort(key=lambda r: (-r["points"], -r["wrong"]))

    out = {"run": run.name, "score": score0, "gap": 100 - score0, "window": run.res["meta"].get("window"), "dataset": run.D,
           "txns": N, "wrong": len(wrong), "oracle": oracle, "light": light, "segments": segs,
           "sum_segment_points": sum(s["points"] for s in segs)}
    if light:
        return out

    # ---- classes
    cls_csv = {r["class"]: r for r in csv.DictReader(open(os.path.join(run.dir, "classes.csv"), encoding="utf-8"))}
    k_cls = sum(1 for r in cls_csv.values() if int(r["golden_rows"]) > 0)
    classes = []
    for c in dev.EXC:
        r = cls_csv[c]
        f1, sup = float(r["f1"]), int(r["golden_rows"])
        p_rows = rescore(golden, fix_class_rows(base, golden, c)) - score0
        tl = [t for t, g in golden.txns.items()
              if any(e == c for _, e in g[0].values()) or (not g[0] and g[2] == c) or any(v[2] == c for v in base.get(t, {}).values())]
        p_lines = rescore(golden, fix_txns(base, golden, tl)) - score0
        classes.append({"class": c, "f1": f1, "support": sup, "pred_rows": int(r["pred_rows"]), "points_rows": p_rows,
                        "points_lines": p_lines, "points_to_f1_09": 100 * S.W_EXC * max(0.0, TARGET_F1 - f1) / k_cls if sup else 0.0,
                        "small": sup < dev.SMALL_N})
    classes.sort(key=lambda r: -r["points_rows"])
    out["classes"] = classes
    out["macro_classes"] = k_cls

    # ---- blocks (reporting only, solution/tools/blocks.yaml)
    blocks = hr_lib.load_blocks()
    for s_ in segs:
        s_["block"] = hr_lib.block_of(s_["segment"], blocks, "segments")
    for c_ in classes:
        c_["block"] = hr_lib.block_of(c_["class"], blocks, "classes")
    bl = {}
    for name in list(blocks) + ["sin_bloque"]:
        bs = [s_ for s_ in segs if s_["block"] == name]
        bc = [c_ for c_ in classes if c_["block"] == name]
        if bs or bc:
            bl[name] = {"segments": [s_["segment"] for s_ in bs], "classes": [c_["class"] for c_ in bc],
                        "wrong": sum(s_["wrong"] for s_ in bs), "points_sum": sum(s_["points"] for s_ in bs)}
    out["blocks"] = bl

    # ---- detail of the top segments (global top N, plus the top 5 of each block for the views)
    top_segs = [s for s in segs if s["wrong"]][:top]
    for name in bl:
        top_segs += [s_ for s_ in segs if s_["block"] == name and s_["wrong"]][:5]
    top_segs = [s_ for s_ in segs if s_ in top_segs]
    if top_segs:
        log("[headroom] building the open-item replay and the pipeline's payer identification ...")
        rp = hr_lib.Replay(run)
        pid = hr_lib.PayerId(run, rp)
    for s in top_segs:
        seg = s["segment"]
        w = sorted(t for t in seg_txns[seg] if t in wrong)
        kinds, payer_how, payer_right = Counter(), Counter(), Counter()
        kind_of = {}
        for t in w:
            k, how, right = recoverability(run, rp, pid, t)
            kind_of[t] = k
            kinds[k] += 1
            if k != "sin partida":
                payer_how[how] += 1
                payer_right["identificado y correcto" if right else ("identificado y distinto del golden" if how != "sin pagador" else "sin pagador")] += 1
        lab_n, lab_w = Counter(), Counter()
        for t in seg_txns[seg]:
            lab = why.get(t, "") or "(sin informe)"
            lab_n[lab] += 1
            if t in wrong:
                lab_w[lab] += 1
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
        nofound = sum(1 for t in w if etype[t] == "a" and (pred.get(t) is None or not pred[t]["items"]))
        dif = difficulty(s["tax"], kinds)
        frac = kinds["recall"] / s["wrong"]
        s["detail"] = {
            "kinds": dict(kinds), "payer_how": dict(payer_how), "payer_right": dict(payer_right), "a_not_found": nofound,
            "passes": [{"label": lab, "wrong": lab_w[lab], "lines": n} for lab, n in sorted(lab_n.items(), key=lambda x: -lab_w[x[0]])
                       if lab_w[lab]][:5],
            "examples": [{"txn": t, "type": etype[t], "kind": kind_of[t], "pass": why.get(t, "")} for t in ex],
            "difficulty": dif, "recall_fraction": frac, "recoverable_points": s["points"] * frac,
            "priority": s["points"] * frac * EASE[dif]}
    return out


# ------------------------------------------------------------------ markdown
def pct(x, n):
    return f"{100 * x / n:.0f} %"


def md_table(headers, rows, align=None):
    align = align or ["l"] + ["r"] * (len(headers) - 1)
    L = ["| " + " | ".join(headers) + " |", "|" + "|".join("---:" if a == "r" else "---" for a in align) + "|"]
    for r in rows:
        L.append("| " + " | ".join(str(c) for c in r) + " |")
    return L


def sentence(s):
    d, tc = s["detail"], s["tax"]
    n = s["wrong"]
    dom, dn = max(tc.items(), key=lambda x: x[1])
    k = d["kinds"]
    lab = d["passes"][0] if d["passes"] else {"label": "-", "wrong": 0}
    if dom == "a" and k.get("impossible", 0) > max(k.get("recall", 0), k.get("ambiguity", 0)):
        how = "casi todo son partidas que no estaban abiertas en ese momento (duplicados, devoluciones, consumidas antes): hace falta modelar el estado del ledger, no más cobertura"
    elif dom == "a" and k.get("ambiguity", 0) > k.get("recall", 0):
        how = "domina la ambigüedad (sin referencia y muchos candidatos, o pagador no identificado): hace falta un criterio de selección entre candidatos o mejor identificación de pagador, no más cobertura"
    elif dom == "a":
        how = "domina el recall (referencia visible o ≤ 5 candidatos del pagador): ampliar la cobertura del matcher"
    elif dom == "b":
        how = "los pares son correctos: basta una regla para la excepción de esas filas"
    elif dom == "c":
        how = "los pares son correctos: revisar el importe asignado a cada partida"
    elif dom == "d":
        how = "no hay partida: mejorar la regla de categoría/excepción del movimiento"
    else:
        how = "los pares emitidos son correctos pero sobran o faltan filas: ajustar el criterio de parada del emparejamiento"
    return f"{pct(dn, n)} son «{ERR_NAME[dom]}» (pase principal `{lab['label']}`, {lab['wrong']} fallos); {how}."


def render_md(R, top):
    segs, gap, score0 = R["segments"], R["gap"], R["score"]
    wrong_segs = [s for s in segs if s["wrong"]]
    L = [f"# Headroom of run `{R['run']}`", "",
         f"- Score del run: **{score0:.2f}** → faltan **{gap:.2f}** puntos hasta 100.",
         f"- Ventana: {R['window']}, {R['txns']} movimientos, dataset `{R['dataset']}`.",
         "- Método: se reconstruye el envío del run con las filas de un segmento (o clase) sustituidas por el golden y se repuntúa "
         "con el scorer oficial (`starter/bankrec_score.py`); puntos = score nuevo − score del run. Cifras exactas, no aproximadas.",
         f"- Comprobaciones: el envío sin modificar reproduce el score de `result.json`; sustituir todos los segmentos da {R['oracle']:.2f}.", ""]
    L += ["## 1. Margen por segmento", "",
          "Puntos que sube el score si los movimientos del segmento pasan a ser exactamente el golden (segmentos sin fallos: 0,00, omitidos).", ""]
    L += md_table(["segmento", "movimientos", "accuracy actual", "mal", "puntos", "% del gap"],
                  [[s["segment"], s["n"], f"{s['accuracy']:.3f}", s["wrong"], f"{s['points']:.2f}", pct(s["points"], gap)]
                   for s in wrong_segs])
    tot = R["sum_segment_points"]
    L += ["", f"**Coherencia**: suma de márgenes por segmento = **{tot:.2f}** frente a **{gap:.2f}** que faltan (100 − {score0:.2f}); "
          f"diferencia {gap - tot:+.2f}. No coinciden exactamente porque el score no es lineal (F1 de asignación y macro-F1 de "
          f"excepciones son cocientes): cada segmento se mide aislado, con el resto tal como está, mientras que corregirlos todos a "
          f"la vez da exactamente 100. Los {R['wrong']} movimientos mal ({100 * R['wrong'] / R['txns']:.2f} % de {R['txns']}) valen por "
          f"sí solos {100 * S.W_TXN * R['wrong'] / R['txns']:.2f} puntos del término de accuracy (peso 0,3).", ""]
    L += ["## 2. Margen por clase de excepción", "",
          "«Filas»: se sustituyen solo las filas de esa clase (las enviadas y las del golden) y el resto del envío queda igual. "
          "«Líneas»: se sustituyen por el golden todos los movimientos donde esa clase aparece en el envío o en el golden "
          "(incluye el efecto sobre la accuracy del movimiento). «A F1 0,9»: puntos del término macro-F1 si la clase llegara a F1 0,9 "
          f"(= 100·0,2·(0,9 − F1)/{R['macro_classes']} clases con soporte; 0 si ya la supera).", ""]
    L += md_table(["clase", "F1 actual", "soporte", "filas enviadas", "puntos (filas)", "puntos (líneas)", "puntos a F1 0,9", "nota"],
                  [[c["class"], f"{c['f1']:.3f}", c["support"], c["pred_rows"], f"{c['points_rows']:.2f}", f"{c['points_lines']:.2f}",
                    f"{c['points_to_f1_09']:.2f}", "(n pequeño)" if c["small"] else ""] for c in R["classes"]],
                  align=["l"] + ["r"] * 6 + ["l"])
    L += [""]
    L += ["## 3. Taxonomía de los movimientos mal, por segmento", "",
          "Cada movimiento mal cae en un único tipo, por este orden: (d) el golden no tiene partidas y la respuesta difiere; "
          "(a) se respondió sin partidas o con un conjunto de partidas que no contiene ni está contenido en el golden; "
          "(e) el conjunto enviado es subconjunto/superconjunto estricto del golden; con el mismo conjunto: (b) alguna excepción difiere, "
          "si no (c) algún importe difiere más allá de la tolerancia.", ""]
    L += md_table(["segmento", "mal", "(a) pares", "(b) excepción", "(c) importe", "(d) categoría", "(e) filas +/−"],
                  [[s["segment"], s["wrong"]] + [s["tax"][k] for k in "abcde"] for s in wrong_segs]
                  + [["**total**", R["wrong"]] + [sum(s["tax"][k] for s in wrong_segs) for k in "abcde"]])
    L += [""]
    tops = [s for s in wrong_segs if "detail" in s][:top]  # the top N always have detail; lower ones only feed the block views
    L += [f"## 4. Detalle del top {len(tops)} por puntos", "",
          "Los ejemplos son bank_txn_id mal, uno de cada tipo de error por turnos, para `solution/tools/inspect_txn.py <run> <id>`. "
          "**Recuperabilidad** de cada movimiento fallado, con el pagador que identifica el propio pipeline (no el del golden; ver "
          "`PayerId` en `hr_lib.py`: partidas que el envío asignó, IBAN, nombre/historial de TRAIN): "
          "*recall* = hay referencia del golden visible en el texto, o el pagador identificado tiene todas las partidas del golden entre "
          "sus abiertas y ≤ 5 en rango; *ambigüedad* = el resto (sin referencia, > 5 candidatos, o pagador no identificado / distinto); "
          "*imposible* = alguna partida del golden no estaba abierta en ese momento (duplicado, consumida, devuelta, fuera del ledger).", ""]
    for s in tops:
        d = s["detail"]
        L += [f"### {s['segment']} — {s['points']:.2f} pts, {s['wrong']} mal de {s['n']}", ""]
        tc = s["tax"]
        L.append("- Tipos de error: " + ", ".join(f"({k}) {tc[k]}" for k in "abcde" if tc[k])
                 + (f". De los (a), {d['a_not_found']} se respondieron sin ninguna partida y {tc['a'] - d['a_not_found']} con otras partidas"
                    if tc["a"] else "") + ".")
        k = d["kinds"]
        L.append("- Recuperabilidad: " + ", ".join(f"{name} {k[key]}" for key, name in
                                                  (("recall", "(a) recall"), ("ambiguity", "(b) ambigüedad"), ("impossible", "(c) imposible"),
                                                   ("sin partida", "sin partida (categoría)")) if k.get(key)) +
                 f". Fracción recall {100 * d['recall_fraction']:.0f} % → **{d['recoverable_points']:.2f} puntos recuperables**.")
        pr = d["payer_right"]
        if pr:
            L.append("- Pagador que identifica el pipeline (movimientos con partidas en el golden): " +
                     ", ".join(f"{n} {v}" for n, v in sorted(pr.items(), key=lambda x: -x[1])) + "; por vía: " +
                     ", ".join(f"{n} {v}" for n, v in sorted(d["payer_how"].items(), key=lambda x: -x[1])) + ".")
        if d["passes"]:
            L.append("- Pase que los resuelve (fallos / movimientos del segmento por pase): " +
                     "; ".join(f"`{p['label']}` {p['wrong']}/{p['lines']}" for p in d["passes"]) + ".")
        L.append("- Ejemplos: " + ", ".join(f"`{e['txn']}` ({e['type']}, {e['kind']}, {e['pass'] or '-'})" for e in d["examples"]))
        L.append("")
    L += ["## 5. Siguiente experimento", "",
          "Dificultad estimada (heurística, no medida): **baja** si ≥ 50 % de los fallos son de excepción/importe/categoría (b/c/d) o ≥ 60 % "
          "son recall; **alta** si ≥ 60 % son ambigüedad o imposibles; **media** el resto. *Puntos recuperables* = puntos × fracción (a) "
          "recall. Prioridad = puntos recuperables × (1 / 0,5 / 0,25 para baja / media / alta).", ""]
    nx = sorted(tops, key=lambda s: -s["detail"]["priority"])
    L += md_table(["#", "segmento", "puntos", "recuperables", "tipo dominante", "recall / ambig. / imposible", "dificultad", "prioridad", "qué probar"],
                  [[i + 1, s["segment"], f"{s['points']:.2f}", f"{s['detail']['recoverable_points']:.2f}",
                    ERR_NAME[max(s["tax"].items(), key=lambda x: x[1])[0]],
                    f"{s['detail']['kinds'].get('recall', 0)} / {s['detail']['kinds'].get('ambiguity', 0)} / {s['detail']['kinds'].get('impossible', 0)}",
                    s["detail"]["difficulty"], f"{s['detail']['priority']:.2f}", sentence(s)] for i, s in enumerate(nx)],
                  align=["r", "l", "r", "r", "l", "r", "l", "r", "l"])
    L += [""]
    return L


def render_block(R, name):
    b = R["blocks"][name]
    gap = R["gap"]
    segs = [s for s in R["segments"] if s["block"] == name and s["wrong"]]
    cls = [c for c in R.get("classes", []) if c["block"] == name]
    L = [f"# Headroom of run `{R['run']}` — bloque `{name}`", "",
         f"- Score {R['score']:.2f}, faltan {gap:.2f} puntos. Este bloque: **{b['points_sum']:.2f}** puntos de segmentos "
         f"({pct(b['points_sum'], gap)} del gap, suma de márgenes aislados), {b['wrong']} movimientos mal.",
         f"- Segmentos del bloque: {len(b['segments'])}; clases: {', '.join(b['classes']) or 'ninguna'}. Mapa en `solution/tools/blocks.yaml` "
         "(solo para informar; el pipeline no lo lee).", ""]
    L += ["## Segmentos", ""]
    L += md_table(["segmento", "movimientos", "accuracy", "mal", "puntos", "recuperables", "(a) pares", "(b) exc.", "(c) imp.", "(d) cat.", "(e) filas"],
                  [[s["segment"], s["n"], f"{s['accuracy']:.3f}", s["wrong"], f"{s['points']:.2f}",
                    f"{s['detail']['recoverable_points']:.2f}" if "detail" in s else "–"] + [s["tax"][k] for k in "abcde"] for s in segs]
                  or [["(sin fallos)"] + [""] * 10])
    L += ["", "## Clases de excepción", ""]
    if cls:
        L += md_table(["clase", "F1", "soporte", "puntos (filas)", "puntos (líneas)", "puntos a F1 0,9"],
                      [[c["class"], f"{c['f1']:.3f}", c["support"], f"{c['points_rows']:.2f}", f"{c['points_lines']:.2f}",
                        f"{c['points_to_f1_09']:.2f}"] for c in cls])
    else:
        L.append("(ninguna clase asignada a este bloque)")
    L += [""]
    return L


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--data", default=None, help="dev dataset dir (default: the one recorded in result.json)")
    ap.add_argument("--top", type=int, default=10, help="segments in the detailed sections (default 10)")
    ap.add_argument("--out", default=None, help="output file (default notes/headroom_<run>.md)")
    a = ap.parse_args()
    R = compute(a.run, a.data, a.top)
    out = a.out or os.path.join(hr_lib.ROOT, "notes", f"headroom_{R['run']}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(render_md(R, a.top)) + "\n")
    stem = os.path.splitext(out)[0]
    json.dump(R, open(stem + ".json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    views = []
    for name in R["blocks"]:
        vp = f"{stem}_{name}.md"
        open(vp, "w", encoding="utf-8").write("\n".join(render_block(R, name)) + "\n")
        views.append(os.path.basename(vp))
    print(f"[headroom] json: {stem}.json; views: {', '.join(views)}")
    print(f"[headroom] {R['run']}: score {R['score']:.2f}, gap {R['gap']:.2f}, sum of segment margins {R['sum_segment_points']:.2f} -> {out}")


if __name__ == "__main__":
    main()
