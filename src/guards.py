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


def check_masters_final(root, phase):
    if phase != "final":
        return
    base, fin = os.path.join(root, "masters"), os.path.join(root, "final", "masters_final")
    if not os.path.isdir(fin):
        sys.exit(f"ERROR (final): {fin} is missing. Refusing to fall back to masters/ without notice.")
    problems = []
    for f in sorted(os.listdir(base)):
        if not f.endswith(".csv"):
            continue
        p = os.path.join(fin, f)
        if not os.path.exists(p):
            problems.append(f"table {f} missing")
            continue
        miss = [c for c in _header(os.path.join(base, f)) if c not in _header(p)]
        if miss:
            problems.append(f"table {f} lacks columns {miss}")
    if problems:
        sys.exit("ERROR (final): masters_final does not match masters/: " + "; ".join(problems))


def check_all(root, phase):
    need_pdftotext(phase, root)
    check_masters_final(root, phase)
