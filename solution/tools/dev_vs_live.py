"""Per-segment table dev-simulation minus LIVE for one model.

    python solution/tools/dev_vs_live.py <run> live_segments.csv [--live-score 82.25]

live_segments.csv: `segment,accuracy` (0-1 or %) copied from the LIVE result page of the SAME model as the run.
"""
import argparse
import csv
import json
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
ap = argparse.ArgumentParser()
ap.add_argument("run")
ap.add_argument("live_csv")
ap.add_argument("--live-score", type=float, default=None)
a = ap.parse_args()
d = a.run if os.path.isdir(a.run) else os.path.join(ROOT, "dev_runs", a.run)
res = json.load(open(os.path.join(d, "result.json")))
n = {r["segment"]: int(r["n"]) for r in csv.DictReader(open(os.path.join(d, "segments.csv")))}
live = {}
for r in csv.DictReader(open(a.live_csv)):
    v = float(str(r["accuracy"]).strip("% "))
    live[r["segment"]] = v / 100 if v > 1 else v
if a.live_score is not None:
    print(f"overall: dev {res['score']:.2f} - LIVE {a.live_score:.2f} = {res['score'] - a.live_score:+.2f}\n")
rows = sorted(((res["segments"][s] - live[s], s) for s in res["segments"] if s in live), key=lambda x: -abs(x[0]))
print(f"{'segment':34s} {'n(dev)':>7} {'dev':>7} {'LIVE':>7} {'dev-LIVE':>9}")
for dlt, s in rows:
    print(f"{s:34s} {n[s]:7d} {res['segments'][s]:7.3f} {live[s]:7.3f} {100 * dlt:+8.1f}pt")
print("\nsegments missing in LIVE csv:", sorted(set(res["segments"]) - set(live)) or "none")
