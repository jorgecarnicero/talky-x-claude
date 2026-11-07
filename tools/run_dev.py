"""Run THIS branch's pipeline on the harness' LIVE-shaped dev dataset and score it with the harness (unchanged).

    python tools/run_dev.py --name base                                   # passes as in run_v35.sh
    python tools/run_dev.py --name v5 --passes batches,remittance,ap_match,nonitem,v3import,ar_match,ar_payer,v3rest

Steps (same as model/v3-harness solution/tools/dev.py::run_v35, but with a configurable pass list):
  1. team v3 (solution/bankrec.py of the harness worktree) -> sub_v3.csv.gz       (cached per dataset unless --fresh-v3)
  2. docs extraction of this branch (src/docs_extract.py)  -> parquet caches inside the dataset
  3. this branch's src/pipeline.py --phase live with --passes
  4. harness `dev.py --score-only` (official scorer + per-segment / per-pass report in <harness>/dev_runs/<name>/)
Data and caches live inside the dataset (BANKREC_DATA / BANKREC_CACHE): nothing leaks from the full TRAIN.
Peak memory of the pipeline step is reported (Windows: PeakWorkingSetSize; elsewhere: ru_maxrss of children).
"""
import argparse
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_PASSES = "batches,remittance,ap_match,nonitem,v3import,ar_match,v3rest"


def run(cmd, env):
    """run a command, return (returncode, seconds, peak_rss_mb)"""
    t0 = time.time()
    p = subprocess.Popen(cmd, env=env)
    peak = None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        h = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, p.pid)
        while p.poll() is None:
            time.sleep(1)
            c = PMC()
            c.cb = ctypes.sizeof(PMC)
            if h and ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                peak = max(peak or 0, c.PeakWorkingSetSize)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
    else:
        p.wait()
        import resource
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024
    return p.returncode, time.time() - t0, (peak or 0) / 2 ** 20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--passes", default=BASE_PASSES)
    ap.add_argument("--data", default=os.path.expanduser("~/hackdata/data_dev"))
    ap.add_argument("--harness", default=os.path.join(REPO, "..", "talky-harness"))
    ap.add_argument("--fresh-v3", action="store_true")
    a = ap.parse_args()
    d, h = os.path.abspath(a.data), os.path.abspath(a.harness)
    py = sys.executable
    cache = os.path.join(d, "solution", "cache", "v35")
    env = {**os.environ, "PYTHONHASHSEED": "0", "BANKREC_DATA": d, "BANKREC_CACHE": cache}
    out = os.path.join(REPO, "runs", a.name)
    os.makedirs(out, exist_ok=True)
    v3_sub = os.path.join(d, "sub_v3_dev.csv.gz")
    v3_why = os.path.join(d, "why_v3_dev.csv")
    if a.fresh_v3 or not os.path.exists(v3_sub):
        rc, s, _ = run([py, os.path.join(h, "solution", "bankrec.py"), "--phase", "live", "--data", d, "--out", v3_sub,
                        "--why", v3_why], env)
        if rc:
            sys.exit("v3 failed")
        print(f"== v3 done in {s:.0f}s")
    for ph in ("train", "live"):
        for kind in ("ra", "pdf"):
            if not os.path.exists(os.path.join(cache, f"docs_{kind}_{ph}.parquet")):
                rc, s, _ = run([py, os.path.join(REPO, "src", "docs_extract.py"), "--data", d, "--phase", ph,
                                "--only", kind], env)
                if rc:
                    sys.exit("docs extraction failed")
    sub, src = os.path.join(out, "sub.csv.gz"), os.path.join(out, "sources.csv")
    rc, secs, peak = run([py, os.path.join(REPO, "src", "pipeline.py"), "--data", d, "--phase", "live", "--passes",
                          a.passes, "--sources", src, "--out", sub], {**env, "V3_SUB": v3_sub})
    if rc:
        sys.exit("pipeline failed")
    with open(os.path.join(out, "perf.txt"), "w") as fh:
        fh.write(f"passes={a.passes}\npipeline_seconds={secs:.1f}\npeak_rss_mb={peak:.0f}\n")
    print(f"== pipeline {secs:.0f}s, peak RSS {peak:.0f} MB")
    rc, _, _ = run([py, os.path.join(h, "solution", "tools", "dev.py"), "--score-only", sub, "--name", a.name,
                    "--out", d], env)
    sys.exit(rc)


if __name__ == "__main__":
    main()
