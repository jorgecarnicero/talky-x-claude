"""Run-time guards for LIVE / FINAL. Standard library only. They only ABORT or WARN; they never change an answer.

  need_pdftotext(phase, root)     live/final: abort without pdftotext on PATH (PDF advices would silently be skipped);
                                  dev datasets (a window.json in the data root) and other phases: warning only.
  check_masters_final(root, phase) final: final/masters_final/ must exist and hold every table of masters/ with every
                                  column of it; never fall back to masters/ silently.
"""
import csv
import os
import shutil
import sys


def is_dev(root):
    """Datasets built by solution/tools/make_dev_dataset.py carry a window.json: they run as --phase live but are dev."""
    return os.path.exists(os.path.join(root, "window.json"))


def need_pdftotext(phase, root):
    if shutil.which("pdftotext"):
        return
    msg = "pdftotext not found on PATH (macOS: brew install poppler): PDF remittance advices / clauses would be skipped"
    if phase in ("live", "final") and not is_dev(root):
        sys.exit(f"ERROR ({phase}): {msg}. Aborting: the output would silently differ from a complete run.")
    print(f"WARNING: {msg}", file=sys.stderr)


def _header(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return next(csv.reader(fh), [])


def masters_report(root):
    """-> list of (table, source, note): where each table of masters/ comes from in a FINAL run (file-by-file overlay)."""
    base, fin = os.path.join(root, "masters"), os.path.join(root, "final", "masters_final")
    rows = []
    for f in sorted(os.listdir(base)):
        if not f.endswith(".csv"):
            continue
        p = os.path.join(fin, f)
        if not os.path.exists(p):
            rows.append((f, "masters/", "not in masters_final: falls back to masters/"))
            continue
        miss = [c for c in _header(os.path.join(base, f)) if c not in _header(p)]
        rows.append((f, "masters_final", f"lacks columns {miss} that masters/ has" if miss else "complete"))
    return rows


def check_masters_final(root, phase):
    """FINAL: abort ONLY if final/masters_final/ is missing altogether. A partial masters_final is legitimate: print which
    tables come from masters_final and which fall back to masters/ and continue. FINAL_STRICT_MASTERS=1: abort on any
    missing table or column instead."""
    if phase != "final":
        return
    fin = os.path.join(root, "final", "masters_final")
    if not os.path.isdir(fin):
        sys.exit(f"ERROR (final): {fin} is missing. Refusing to fall back to masters/ without notice.")
    rows = masters_report(root)
    w = max(len(r[0]) for r in rows) if rows else 10
    print("masters for FINAL (final/masters_final overlays masters/ file by file):")
    for t, src, note in rows:
        print(f"   {t:<{w}}  {src:<14}  {note}")
    bad = [r for r in rows if r[2] != "complete"]
    if bad and os.environ.get("FINAL_STRICT_MASTERS") == "1":
        sys.exit("ERROR (final, FINAL_STRICT_MASTERS=1): masters_final is not complete: " + "; ".join(f"{t}: {n}" for t, _, n in bad))
    if bad:
        print(f"NOTE: {len(bad)} table(s) are not complete in masters_final (see above); continuing (set FINAL_STRICT_MASTERS=1 to abort).")


def check_all(root, phase):
    need_pdftotext(phase, root)
    check_masters_final(root, phase)
