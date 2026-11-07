"""Pipeline checkpoints: snapshot the context after the early passes, reuse it when nothing they depend on changed.

Opt-in with PIPE_CKPT=1 (pipeline.py). Speeds up iterations on the LATER passes (v3import, ar_match, ar_bigpool,
exc_fix, ...): the early, slow passes (batches, remittance, ap_match, nonitem, ar_multi, ar_pdf) are skipped.

Safety: the snapshot is reused only when its key matches exactly. The key hashes
  * the source of every local module the early passes (and data.py) import, followed recursively,
  * the values of every environment flag those modules read (os.environ.get("X") / os.getenv("X")),
  * phase, window (--from/--to), data root, cache dir, the exact list of early passes,
  * size + mtime of the cached document tables (docs_*.parquet) they read,
  * Python and pandas versions.
Editing a later pass does not invalidate the snapshot; editing anything the early passes depend on does.
The whole Ctx is pickled as ONE object (objects in ctx.cache that point back to ctx stay consistent).
Writes are atomic (tmp + rename); any failure falls back to a normal run.
"""
import ast
import hashlib
import json
import os
import pickle
import re
import sys
import time

SRC = os.path.dirname(os.path.abspath(__file__))
KEEP = int(os.environ.get("PIPE_CKPT_KEEP", "4"))
_ENV_RE = re.compile(r"""(?:environ\.get|getenv)\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']|environ\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]""")


def _module_file(name):
    """Local module name ('data', 'passes.batches', 'batches') -> file path under src/, or None (stdlib/3rd party)."""
    parts = name.split(".")
    for base in (SRC, os.path.join(SRC, "passes")):
        p = os.path.join(base, *parts) + ".py"
        if os.path.isfile(p):
            return p
        p = os.path.join(base, *parts, "__init__.py")
        if os.path.isfile(p):
            return p
    return None


def local_closure(start_modules):
    """All local source files reachable through imports (including imports inside functions)."""
    seen, todo = set(), [m for m in start_modules]
    files = set()
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        seen.add(m)
        f = _module_file(m)
        if not f or f in files:
            continue
        files.add(f)
        tree = ast.parse(open(f, encoding="utf-8").read(), f)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                todo += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                todo.append(node.module)
                todo += [f"{node.module}.{a.name}" for a in node.names]   # from passes import x
    return sorted(files)


def make_key(a, prefix, cache_dir):
    files = local_closure(["data"] + [f"passes.{p}" for p in prefix])
    h_files, env_names = {}, set()
    for f in files:
        src = open(f, "rb").read()
        h_files[os.path.relpath(f, SRC).replace("\\", "/")] = hashlib.sha256(src).hexdigest()
        for m in _ENV_RE.finditer(src.decode("utf-8", "replace")):
            env_names.add(m.group(1) or m.group(2))
    docs = {}
    if os.path.isdir(cache_dir):
        for fn in sorted(os.listdir(cache_dir)):
            if fn.startswith("docs_") and fn.endswith(".parquet"):
                st = os.stat(os.path.join(cache_dir, fn))
                docs[fn] = [st.st_size, int(st.st_mtime)]
    import pandas
    payload = {
        "v": 1, "phase": a.phase, "from": a.dfrom, "to": a.dto,
        "data": os.path.abspath(a.data), "cache": os.path.abspath(cache_dir), "prefix": list(prefix),
        "files": h_files, "env": {k: os.environ.get(k) for k in sorted(env_names)}, "docs": docs,
        # flags that point to a file (e.g. V3_SUB, read by ar_multi before v3import): hash the CONTENT, not the path
        "env_files": {k: _file_sha(os.environ[k]) for k in sorted(env_names)
                      if os.environ.get(k) and os.path.isfile(os.environ[k])},
        "python": sys.version, "pandas": pandas.__version__,
    }
    blob = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:20], payload


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def path_for(cache_dir, key):
    return os.path.join(cache_dir, "checkpoints", f"ckpt_{key}.pkl")


def load(path):
    t0 = time.time()
    with open(path, "rb") as fh:
        ctx = pickle.load(fh)
    print(f"  checkpoint: resumed {os.path.basename(path)} ({os.path.getsize(path) / 1e9:.2f} GB) "
          f"[{time.time() - t0:.0f}s]", flush=True)
    return ctx


def save(ctx, path, payload):
    t0 = time.time()
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            pickle.dump(ctx, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
        with open(path[:-4] + ".json", "w") as fh:   # human-readable record of what the key covers
            json.dump(payload, fh, indent=1, sort_keys=True)
    except Exception as e:  # noqa: BLE001  -- never fail the run because of a checkpoint
        print(f"  checkpoint: NOT saved ({type(e).__name__}: {e})", flush=True)
        if os.path.exists(tmp):
            os.remove(tmp)
        return
    print(f"  checkpoint: saved {os.path.basename(path)} ({os.path.getsize(path) / 1e9:.2f} GB) "
          f"[{time.time() - t0:.0f}s]", flush=True)
    # keep only the newest KEEP snapshots
    snaps = sorted((os.path.join(d, f) for f in os.listdir(d) if f.endswith(".pkl")), key=os.path.getmtime)
    for old in snaps[:-KEEP]:
        for ext in (".pkl", ".json"):
            p = old[:-4] + ext
            if os.path.exists(p):
                os.remove(p)
