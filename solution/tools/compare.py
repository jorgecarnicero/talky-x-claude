"""Accept or reject a candidate dev run against a baseline run.

    python solution/tools/compare.py <baseline> <candidate>      # run names under dev_runs/ (or paths to run folders)

ACEPTAR iff the overall score goes up AND no segment that the baseline gets >90 % right drops by more than 0.5 points
(accuracy points, i.e. 0.005); NEUTRO if the score is unchanged and nothing protected drops; else RECHAZAR.
Exit code 0 = ACEPTAR, 3 = NEUTRO, 1 = RECHAZAR. Also lists the biggest segment movements.
"""
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
HIGH, MAX_DROP = 0.90, 0.005


def load(x):
    d = x if os.path.isdir(x) else os.path.join(ROOT, "dev_runs", x)
    with open(os.path.join(d, "result.json")) as fh:
        return json.load(fh)


def verdict(b, c):
    """('ACEPTAR' | 'NEUTRO' | 'RECHAZAR', reasons, protected-segment violations)"""
    deltas = []
    for s, ab in b["segments"].items():
        ac = c["segments"].get(s, 0.0)
        deltas.append((ac - ab, s, ab, ac))
    deltas.sort()
    viol = [(d, s, ab, ac) for d, s, ab, ac in deltas if ab > HIGH and -d > MAX_DROP + 1e-12]
    why = [f"segment {s} (>90 %) dropped {-100 * d:.2f} pts" for d, s, ab, ac in viol]
    delta = round(c["score"] - b["score"], 2)
    if delta > 0 and not viol:
        return "ACEPTAR", [], deltas
    if delta == 0 and not viol:
        return "NEUTRO", ["score unchanged"], deltas
    if delta <= 0:
        why.insert(0, "overall score did not go up" if delta == 0 else f"overall score went down ({delta:+.2f})")
    return "RECHAZAR", why, deltas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("baseline")
    ap.add_argument("candidate")
    a = ap.parse_args()
    b, c = load(a.baseline), load(a.candidate)
    if b["meta"].get("window") != c["meta"].get("window"):
        sys.exit(f"different windows: {b['meta'].get('window')} vs {c['meta'].get('window')}")
    print(f"score {b['score']:.2f} -> {c['score']:.2f}  ({c['score'] - b['score']:+.2f})")
    for k in ("allocation_f1", "txn_accuracy", "exception_macro_f1"):
        print(f"  {k:20s} {b['metrics'][k]:.4f} -> {c['metrics'][k]:.4f}  ({c['metrics'][k] - b['metrics'][k]:+.4f})")
    print("\nexception classes (F1 baseline -> candidate, golden support):")
    for k, v in c.get("classes", {}).items():
        bf = b.get("classes", {}).get(k, {}).get("f1", 0.0)
        flag = "   (poco fiable: n<100)" if v["support"] < 100 else ""
        print(f"  {k:20s} {bf:.3f} -> {v['f1']:.3f}  ({v['f1'] - bf:+.3f})  n={v['support']}{flag}")
    label, why, deltas = verdict(b, c)
    print("\nlargest drops:")
    for d, s, ab, ac in deltas[:6]:
        print(f"  {s:34s} {ab:.3f} -> {ac:.3f}  ({100 * d:+.1f} pts){'   <-- protected segment' if ab > HIGH else ''}")
    print("largest gains:")
    for d, s, ab, ac in deltas[::-1][:6]:
        print(f"  {s:34s} {ab:.3f} -> {ac:.3f}  ({100 * d:+.1f} pts)")
    print("\n" + label + ("" if not why else ": " + "; ".join(why)))
    sys.exit({"ACEPTAR": 0, "NEUTRO": 3, "RECHAZAR": 1}[label])


if __name__ == "__main__":
    main()
