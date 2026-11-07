"""informe.md of a branch test: verdict, score, delta per segment and per exception class, good->bad / bad->good lines.
Used by test_branch.py (not meant to be run by hand)."""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dev  # noqa: E402


def _wrong(run_dir):
    with open(os.path.join(run_dir, "wrong.csv"), newline="", encoding="utf-8") as fh:
        return {r["bank_txn_id"]: r["segment"] for r in csv.DictReader(fh)}


def _why(run_dir):
    p = os.path.join(run_dir, "why.csv")
    if not os.path.exists(p):
        return {}
    with open(p, newline="", encoding="utf-8") as fh:
        return {r["bank_txn_id"]: r["why"] for r in csv.DictReader(fh)}


def write(out_dir, info, base_dir, cand_dir, data_dir, base, cand, label, why, deltas):
    os.makedirs(out_dir, exist_ok=True)
    wb, wc = _wrong(base_dir), _wrong(cand_dir)
    good_to_bad = sorted(set(wc) - set(wb))
    bad_to_good = sorted(set(wb) - set(wc))
    cats = [r["category"] for r in csv.DictReader(open(os.path.join(data_dir, "masters", "categories.csv"), encoding="utf-8"))]
    golden = dev.S.load_golden(os.path.join(data_dir, "golden_dev.csv"), os.path.join(data_dir, "live", "bank_transactions.csv.gz"),
                               dev.EXC, cats)
    pb, pc = dev.read_sub(os.path.join(base_dir, "sub.csv.gz")), dev.read_sub(os.path.join(cand_dir, "sub.csv.gz"))
    cw = _why(cand_dir)
    for name, ids in (("cambios_bien_a_mal.csv", good_to_bad), ("cambios_mal_a_bien.csv", bad_to_good)):
        with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["bank_txn_id", "segment", "golden", "baseline", "candidate", "matcher_candidate"])
            for t in ids:
                w.writerow([t, golden.txns[t][3], dev.fmt_g(golden.txns[t]), dev.fmt_p(pb.get(t)), dev.fmt_p(pc.get(t)), cw.get(t, "")])

    def seg_count(ids):
        c = {}
        for t in ids:
            c[golden.txns[t][3]] = c.get(golden.txns[t][3], 0) + 1
        return sorted(c.items(), key=lambda x: -x[1])

    n_by_seg = {}
    with open(os.path.join(cand_dir, "segments.csv"), newline="", encoding="utf-8") as fh:
        n_by_seg = {r["segment"]: int(r["n"]) for r in csv.DictReader(fh)}
    L = [f"# Test of `{info['rama']}` @ {info['commit'][:10]}", "",
         f"## Veredicto: **{label}**" + ("" if not why else " (" + "; ".join(why) + ")"), "",
         f"| | baseline | candidato | delta |", "|---|---:|---:|---:|",
         f"| **nota** | {base['score']:.2f} | {cand['score']:.2f} | **{cand['score'] - base['score']:+.2f}** |"]
    for k in ("allocation_f1", "txn_accuracy", "exception_macro_f1"):
        L.append(f"| {k} | {base['metrics'][k]:.4f} | {cand['metrics'][k]:.4f} | {cand['metrics'][k] - base['metrics'][k]:+.4f} |")
    L += ["", f"- ventana: {info['window']}{' (SELLADA)' if info['sealed'] else ''}; minutos del candidato: {info['minutes']:.1f}",
          f"- rama: `{info['rama']}` commit `{info['commit']}`; integracion `{info['integration']}` commit `{info['integration_commit'][:10]}`",
          f"- codigo probado (arbol de la fusion): `{info['tree']}`; baseline: arbol `{info['base_tree']}` (`{os.path.basename(base_dir)}`)",
          f"- clave de salida (solo ficheros que afectan a la salida + datos): candidato `{info['key'][:12]}`, baseline `{info['base_key'][:12]}`",
          f"- PYTHONHASHSEED=0, cache aislada: `{info['view']}`", "",
          f"## Cambios por linea: bien->mal {len(good_to_bad)}, mal->bien {len(bad_to_good)}  (neto {len(bad_to_good) - len(good_to_bad):+d})", ""]
    for ttl, ids in (("bien -> mal", good_to_bad), ("mal -> bien", bad_to_good)):
        L.append(f"**{ttl}** por segmento: " + (", ".join(f"{s} {n}" for s, n in seg_count(ids)[:12]) or "ninguno"))
        L.append("")
    L += ["Detalle completo en `cambios_bien_a_mal.csv` y `cambios_mal_a_bien.csv` (golden, baseline, candidato, matcher).", "",
          "## Delta por segmento (accuracy, solo los que cambian; * = protegido, >90 % en baseline)", "",
          "| segmento | n | baseline | candidato | delta (pts) |", "|---|---:|---:|---:|---:|"]
    moved = [(d, s, ab, ac) for d, s, ab, ac in deltas if abs(d) > 1e-9]
    for d, s, ab, ac in sorted(moved, key=lambda x: x[0]):
        L.append(f"| {s}{' *' if ab > 0.9 else ''} | {n_by_seg.get(s, '')} | {ab:.3f} | {ac:.3f} | {100 * d:+.1f} |")
    if not moved:
        L.append("| (sin cambios) | | | | |")
    L += ["", "## Delta por clase de excepcion (F1; < 100 filas = poco fiable)", "",
          "| clase | filas golden | baseline | candidato | delta |", "|---|---:|---:|---:|---:|"]
    for k, v in cand.get("classes", {}).items():
        bf = base.get("classes", {}).get(k, {}).get("f1", 0.0)
        L.append(f"| {k} | {v['support']}{' (poco fiable)' if v['support'] < 100 else ''} | {bf:.3f} | {v['f1']:.3f} | {v['f1'] - bf:+.3f} |")
    try:  # remaining headroom of the top 5 after the change (diagnostic: a failure here never changes the verdict)
        import headroom
        L += headroom.remaining_top(base_dir, cand_dir, data_dir, 5, log=lambda *a: None)
    except Exception as ex:  # noqa: BLE001
        L += ["", "## Margen restante tras el cambio", "", f"(no disponible: {type(ex).__name__}: {ex})", ""]
    open(os.path.join(out_dir, "informe.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    return len(good_to_bad), len(bad_to_good)
