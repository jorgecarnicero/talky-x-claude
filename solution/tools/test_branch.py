#!/usr/bin/env python3
"""Test a teammate's branch on the LIVE simulation with one command (see test_branch.sh).

    solution/tools/test_branch.sh <branch> [--sealed] [--force]
    solution/tools/test_branch.sh --merge <branch>

1. git fetch; temporary worktree of origin/<branch> under ~/hackdata/wt/ (a local branch is used if it is not on origin);
2. trial merge of the integration branch (INTEGRATION_BRANCH, default model/v3-harness); conflicts -> list the files and stop;
3. run dev.py on the merged code (harness, scorer and dataset builder always come from THIS checkout), PYTHONHASHSEED=0,
   with a cache directory keyed by the hash of the cache-producing code, and compare.py's verdict against the baseline of the
   integration tip (generated once per OUTPUT KEY and kept in dev_runs/_baseline_<key>_<window>; the key hashes only
   solution/bankrec.py, src/**, the scorer, make_dev_dataset.py and the dev data, so tools/notes/README commits do not invalidate it);
4. dev_runs/<branch>_<commit>/informe.md + a row in dev_runs/registro.csv (+ notes/registro_pruebas.md);
5. --sealed: same on the sealed window, once per tested tree, logged in dev_runs/sealed_log.txt;
6. --merge: merges the branch into the integration branch only if the last verdict for that exact commit was ACEPTAR
   (and the integration CODE has not changed since: docs/tools commits do not count), and notes it in SUBMISSIONS.md as a candidate for LIVE (never uploads).
Only one test runs at a time (lock file ~/hackdata/wt/.test.lock): a second one waits.
"""
import argparse
import csv
import datetime
import fcntl
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import branch_report  # noqa: E402
import compare  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
INTEGRATION = os.environ.get("INTEGRATION_BRANCH", "model/v3-harness")
HOME = os.path.expanduser("~/hackdata")
WT = os.path.join(HOME, "wt")
VIEWS = os.path.join(HOME, "wt_data")
RUNS = os.path.join(REPO, "dev_runs")
LOCK = os.path.join(WT, ".test.lock")
PY = os.environ.get("BANKREC_PY") or sys.executable
WINDOWS = {"default": ("2025-04-01", "2025-05-31", os.path.join(HOME, "data_dev")),
           "sealed": ("2025-01-01", "2025-02-28", os.path.join(HOME, "data_dev_sealed"))}
REG_COLS = ["fecha", "rama", "commit", "arbol", "base_arbol", "clave", "base_clave", "ventana", "nota", "delta", "veredicto", "minutos"]
# Files that determine the pipeline's OUTPUT: the baseline / "same code" key depends on these (and on the dev data) only,
# so a commit touching tools/dev.py, compare.py, test_branch.*, notes/ or the README does not invalidate a baseline.
KEY_FILES = ("solution/bankrec.py", "starter/bankrec_score.py", "solution/tools/make_dev_dataset.py")
KEY_DIRS = ("src/",)


def say(*a):
    print("[test_branch]", *a, flush=True)


def git(*args, cwd=REPO, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r


def safe(x):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", x)


# ------------------------------------------------------------------ lock
class Lock:
    def __init__(self, what):
        os.makedirs(WT, exist_ok=True)
        self.fh = open(LOCK, "a+")
        t0, said = time.time(), False
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                self.fh.seek(0)
                holder = self.fh.read().strip() or "another test"
                if not said or int(time.time() - t0) % 120 == 0:
                    say(f"another test is running ({holder}); waiting for it to finish (one test at a time) ...")
                    said = True
                time.sleep(5)
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(f"{what} (pid {os.getpid()}, since {datetime.datetime.now():%H:%M:%S})")
        self.fh.flush()

    def release(self):
        try:
            self.fh.seek(0)
            self.fh.truncate()
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()


# ------------------------------------------------------------------ refs / worktrees
def resolve(branch):
    for ref, src in ((f"origin/{branch}", "origin"), (branch, "local")):
        r = git("rev-parse", "--verify", "--quiet", ref + "^{commit}", check=False)
        if r.returncode == 0:
            return ref, r.stdout.strip(), src
    sys.exit(f"branch {branch} not found (neither origin/{branch} nor local {branch})")


def integration_tip():
    r = git("rev-parse", "--verify", "--quiet", INTEGRATION + "^{commit}", check=False)
    ref = INTEGRATION if r.returncode == 0 else "origin/" + INTEGRATION
    commit = git("rev-parse", ref + "^{commit}").stdout.strip()
    o = git("rev-parse", "--verify", "--quiet", "origin/" + INTEGRATION + "^{commit}", check=False)
    if o.returncode == 0 and o.stdout.strip() != commit:
        n = git("rev-list", "--count", f"origin/{INTEGRATION}..{ref}").stdout.strip()
        say(f"NOTE: local {INTEGRATION} differs from origin/{INTEGRATION} ({n} commits ahead); the LOCAL tip is the integration baseline")
    return ref, commit


def add_worktree(path, commit):
    remove_worktree(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    git("worktree", "add", "--detach", "-q", path, commit)


def remove_worktree(path):
    if os.path.exists(path):
        git("worktree", "remove", "--force", path, check=False)
        shutil.rmtree(path, ignore_errors=True)
    git("worktree", "prune", check=False)


def cache_code_hash(wt):
    files = {"solution/bankrec.py", "src/data.py", "src/docs_extract.py", "src/docs_clauses.py", "src/pdfinfo.py"}
    files |= {os.path.relpath(f, wt) for f in glob.glob(os.path.join(wt, "src", "docs_*.py"))}
    h = hashlib.sha256()
    for f in sorted(files):
        p = os.path.join(wt, f)
        h.update(f.encode())
        h.update(open(p, "rb").read() if os.path.exists(p) else b"<absent>")
    return h.hexdigest()[:12]


def output_key(rev, cwd, win, index=False):
    """Hash of the blobs of the output-affecting files of a commit/tree (or of the index) + the dev dataset fingerprint."""
    if index:
        out = git("ls-files", "-s", cwd=cwd).stdout.splitlines()
        ents = [(l.split("\t", 1)[1], l.split("\t", 1)[0].split()[1]) for l in out]
    else:
        out = git("ls-tree", "-r", rev, cwd=cwd).stdout.splitlines()
        ents = [(l.split("\t", 1)[1], l.split("\t", 1)[0].split()[2]) for l in out]
    h = hashlib.sha256()
    for path, blob in sorted(ents):
        if path in KEY_FILES or path.startswith(KEY_DIRS):
            h.update(f"{path} {blob}\n".encode())
    wj = os.path.join(WINDOWS[win][2], "window.json")
    h.update(open(wj, "rb").read() if os.path.exists(wj) else b"<no dataset>")
    return h.hexdigest()


def make_view(code_hash, win):
    """A data directory for one cache key: symlinks to the shared dev dataset + its own solution/cache."""
    start, end, shared = WINDOWS[win]
    ensure_dataset(win)
    view = os.path.join(VIEWS, f"{code_hash}-{win}")
    os.makedirs(os.path.join(view, "solution", "cache"), exist_ok=True)
    for n in ("train", "live", "masters", "golden_dev.csv", "window.json"):
        link = os.path.join(view, n)
        if os.path.islink(link) or os.path.exists(link):
            os.remove(link)
        os.symlink(os.path.join(shared, n), link)
    return view


def ensure_dataset(win):
    import json
    start, end, shared = WINDOWS[win]
    wj = os.path.join(shared, "window.json")
    if os.path.exists(wj):
        w = json.load(open(wj))
        if (w["start"], w["end"]) == (start, end):
            return
    say(f"building the {win} dev dataset in {shared} ...")
    if subprocess.run([PY, os.path.join(HERE, "make_dev_dataset.py"), "--start", start, "--end", end, "--out", shared,
                       "--src", os.path.join(HOME, "data")]).returncode:
        sys.exit("dataset build failed")


def run_dev(wt, name, view, win):
    start, end, _ = WINDOWS[win]
    cmd = [PY, os.path.join(HERE, "dev.py"), "--code-root", wt, "--runs-dir", RUNS, "--name", name, "--out", view]
    cmd += ["--sealed"] if win == "sealed" else ["--start", start, "--end", end]
    t0 = time.time()
    r = subprocess.run(cmd, env={**os.environ, "PYTHONHASHSEED": "0"})
    if r.returncode:
        sys.exit(f"dev.py failed for {name}")
    return (time.time() - t0) / 60


# ------------------------------------------------------------------ registry
def read_registro():
    p = os.path.join(RUNS, "registro.csv")
    if not os.path.exists(p):
        return []
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def add_registro(row):
    p = os.path.join(RUNS, "registro.csv")
    os.makedirs(RUNS, exist_ok=True)
    rows = read_registro() + [row]
    with open(p, "w", newline="", encoding="utf-8") as fh:  # rewritten whole: older rows may lack newer columns
        w = csv.DictWriter(fh, REG_COLS, restval="")
        w.writeheader()
        w.writerows(rows)
    md = ["# Registro de pruebas de ramas", "", "Generado por `solution/tools/test_branch.sh` desde `dev_runs/registro.csv`.", "",
          "| fecha | rama | commit | ventana | nota | delta | veredicto | min |", "|---|---|---|---|---:|---:|---|---:|"]
    for r in rows:
        md.append(f"| {r['fecha']} | {r['rama']} | {r['commit'][:10]} | {r['ventana']} | {r['nota']} | {r['delta']} | {r['veredicto']} | {r['minutos']} |")
    os.makedirs(os.path.join(REPO, "notes"), exist_ok=True)
    open(os.path.join(REPO, "notes", "registro_pruebas.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")


# ------------------------------------------------------------------ test
def test(branch, sealed, force):
    win = "sealed" if sealed else "default"
    start, end, shared = WINDOWS[win]
    git("fetch", "--prune", "-q", "origin", check=False)
    ref, commit, src = resolve(branch)
    iref, icommit = integration_tip()
    itree = git("rev-parse", icommit + "^{tree}").stdout.strip()
    ensure_dataset(win)
    ikey = output_key(icommit, REPO, win)
    say(f"branch {branch} = {commit[:10]} ({src}); integration {INTEGRATION} = {icommit[:10]}; window {start}..{end}"
        f"{' (SEALED)' if sealed else ''}")
    if sealed:
        sl = os.path.join(RUNS, "sealed_log.txt")
        done = open(sl).read() if os.path.exists(sl) else ""

    wt = os.path.join(WT, safe(branch))
    base_wt = None
    try:
        add_worktree(wt, commit)
        if git("status", "--porcelain", cwd=wt).stdout.strip():
            sys.exit("worktree is not clean")
        m = git("merge", "--no-commit", "--no-ff", icommit, cwd=wt, check=False)
        if m.returncode:
            files = git("diff", "--name-only", "--diff-filter=U", cwd=wt).stdout.split()
            git("merge", "--abort", cwd=wt, check=False)
            say(f"MERGE CONFLICT with {INTEGRATION}; resolve it in the branch first. Conflicting files:")
            for f in files:
                print("   ", f)
            sys.exit(2)
        if git("status", "--porcelain", cwd=wt).stdout.strip():
            git("-c", "user.name=test-branch", "-c", "user.email=test-branch@localhost", "commit", "-q", "-m",
                f"test merge {INTEGRATION} into {branch}", cwd=wt)
        tree = git("rev-parse", "HEAD^{tree}", cwd=wt).stdout.strip()
        if git("status", "--porcelain", cwd=wt).stdout.strip():
            sys.exit("worktree is not clean after the merge")
        ckey = output_key("HEAD", wt, win)
        say(f"tested code: tree {tree[:12]} (branch commit {commit[:10]} + {INTEGRATION} {icommit[:10]}); output key {ckey[:10]} "
            f"(baseline {ikey[:10]}); PYTHONHASHSEED=0")
        if sealed and tree in done and not force:
            sys.exit(f"the sealed window was already used for this exact tree ({tree[:12]}), see dev_runs/sealed_log.txt "
                     f"(one sealed run per tree; --force overrides)")

        # ---- baseline of the integration tip (per tree hash)
        base_name = f"_baseline_{ikey[:10]}_{win}"
        base_dir = os.path.join(RUNS, base_name)
        if not os.path.exists(os.path.join(base_dir, "result.json")):
            say(f"no baseline for output key {ikey[:10]} (integration tip {icommit[:10]}) on the {win} window: generating it ...")
            base_wt = os.path.join(WT, "_integration_" + ikey[:10])
            add_worktree(base_wt, icommit)
            bv = make_view(cache_code_hash(base_wt), win)
            mins = run_dev(base_wt, base_name, bv, win)
            say(f"baseline done in {mins:.1f} min")
        else:
            say(f"baseline found: {base_name}")

        # ---- candidate
        name = f"{safe(branch)}_{commit[:8]}" + ("_sealed" if sealed else "")
        cand_dir = os.path.join(RUNS, name)
        view = make_view(cache_code_hash(wt), win)
        if ckey == ikey and not force:
            say("the merged code has the same output key as the integration tip (only docs / tools differ): nothing to run "
                "(--force runs it anyway)")
            shutil.rmtree(cand_dir, ignore_errors=True)
            shutil.copytree(base_dir, cand_dir)
            mins = 0.0
        else:
            mins = run_dev(wt, name, view, win)
        b, c = compare.load(base_dir), compare.load(cand_dir)
        label, why, deltas = compare.verdict(b, c)
        info = {"rama": branch, "commit": commit, "integration": INTEGRATION, "integration_commit": icommit, "tree": tree,
                "base_tree": itree, "key": ckey, "base_key": ikey, "window": f"{start}..{end}", "sealed": sealed, "minutes": mins, "view": view}
        gb, bg = branch_report.write(cand_dir, info, base_dir, cand_dir, shared, b, c, label, why, deltas)
        add_registro({"fecha": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "rama": branch, "commit": commit, "arbol": tree,
                      "base_arbol": itree, "clave": ckey, "base_clave": ikey, "ventana": win, "nota": f"{c['score']:.2f}", "delta": f"{c['score'] - b['score']:+.2f}",
                      "veredicto": label, "minutos": f"{mins:.1f}"})
        if sealed:
            with open(os.path.join(RUNS, "sealed_log.txt"), "a") as fh:
                fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M} {branch} {commit[:10]} tree {tree} score {c['score']:.2f} "
                         f"delta {c['score'] - b['score']:+.2f} {label}\n")
        say(f"{label}: {c['score']:.2f} vs baseline {b['score']:.2f} ({c['score'] - b['score']:+.2f}); bien->mal {gb}, mal->bien {bg}; "
            f"{mins:.1f} min")
        if why:
            say("   " + "; ".join(why))
        say(f"report: {os.path.join(cand_dir, 'informe.md')}")
        return label
    finally:
        remove_worktree(wt)
        if base_wt:
            remove_worktree(base_wt)


# ------------------------------------------------------------------ merge
def merge(branch):
    git("fetch", "--prune", "-q", "origin", check=False)
    ref, commit, src = resolve(branch)
    iref, icommit = integration_tip()
    itree = git("rev-parse", icommit + "^{tree}").stdout.strip()
    rows = [r for r in read_registro() if r["rama"] == branch and r["commit"] == commit and r["ventana"] == "default"]
    if not rows:
        sys.exit(f"no test on record for {branch} @ {commit[:10]}: run test_branch.sh {branch} first")
    last = rows[-1]
    if last["veredicto"] != "ACEPTAR":
        sys.exit(f"last verdict for {branch} @ {commit[:10]} is {last['veredicto']}, not ACEPTAR: not merging")
    ikey = output_key(icommit, REPO, "default")
    if (last.get("base_clave") or "") != ikey:
        sys.exit(f"the integration code changed since the test (baseline key {(last.get('base_clave') or '-')[:10]}, now {ikey[:10]}): test again")
    cur = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if cur != INTEGRATION:
        sys.exit(f"check out {INTEGRATION} first (current: {cur})")
    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        sys.exit("tracked files have uncommitted changes in this checkout; commit or stash them first:\n"
                 + git("status", "--short", "--untracked-files=no").stdout)
    m = git("merge", "--no-ff", "--no-commit", commit, check=False)
    if m.returncode:
        git("merge", "--abort", check=False)
        sys.exit("merge conflict (it should not happen after a passed test): " + m.stdout.strip())
    got = output_key(None, REPO, "default", index=True)
    if got != last.get("clave"):
        git("merge", "--abort", check=False)
        sys.exit(f"the merged code (key {got[:10]}) differs from the tested code (key {(last.get('clave') or '-')[:10]}): aborted, nothing changed")
    sub = os.path.join(REPO, "SUBMISSIONS.md")
    txt = open(sub, encoding="utf-8").read()
    head = "## Candidatos a subir a LIVE (no subidos)"
    line = (f"- `{branch}` @ `{commit[:10]}`: dev {last['nota']} ({last['delta']} vs baseline), veredicto ACEPTAR el {last['fecha']}. "
            f"**Candidato a subir a LIVE; todavia NO subido.**\n")
    if head not in txt:
        txt = txt.rstrip("\n") + f"\n\n{head}\n\n"
    txt = txt.rstrip("\n") + "\n" + line
    open(sub, "w", encoding="utf-8").write(txt)
    git("add", "SUBMISSIONS.md")
    git("commit", "-q", "-m", f"Merge {branch} (dev {last['nota']}, {last['delta']}): candidate for LIVE, not uploaded")
    say(f"merged {branch} @ {commit[:10]} into {INTEGRATION} and noted it in SUBMISSIONS.md (not pushed, not uploaded)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("branch")
    ap.add_argument("--sealed", action="store_true", help="run the sealed window (once per tested tree; logged)")
    ap.add_argument("--merge", action="store_true", help="merge the branch if its last verdict is ACEPTAR")
    ap.add_argument("--force", action="store_true", help="run even if the merged tree equals the integration tip / the sealed window was used")
    a = ap.parse_args()
    lock = Lock(("merge " if a.merge else "test ") + a.branch)
    try:
        if a.merge:
            merge(a.branch)
        else:
            test(a.branch, a.sealed, a.force)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
