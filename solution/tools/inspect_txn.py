"""Everything about one bank line of a dev run: the line, the golden, what the pipeline answered, which pass explained it and the
open items of the payer the pipeline identified (plus, for diagnosis, those of the golden's party when they differ).

    python solution/tools/inspect_txn.py v35 BT-2025-0092792 [more ids]
    python solution/tools/inspect_txn.py v35 --segment ar_overpayment --worst 5     # the 5 largest wrong lines of the segment
    options: --cands N (max candidate rows shown, default 15)  --data DIR

Diagnostic only: reads the golden and the segment; nothing here feeds the pipeline.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hr_lib  # noqa: E402

dev = hr_lib.dev


def show(run, rp, pid, t, max_cands):
    g = run.golden.txns[t]
    b = run.bank.loc[t]
    p = run.pred.get(t)
    out = [f"=== {t}  [{'OK' if run.ok(t) else 'WRONG'}] ===",
           f"line      : {b.booking_date}  {b.amount} {b.currency}  entity {b.entity}  account {b.account_id}",
           f"text      : {b.text}",
           f"counterp. : name '{b.counterparty_name}'  account '{b.counterparty_account}'  ref1 '{b.ref1}'  ref2 '{b.ref2}'",
           f"remittance: {b.remittance_info}  (end_to_end {b.end_to_end_id})",
           f"golden    : segment {g[3]} | " + dev.fmt_g(g).replace("; ", "\n            "),
           f"predicted : " + dev.fmt_p(p).replace("; ", "\n            "),
           f"pass      : {run.why.get(t, '(no why.csv in this run)') or '(none)'}"]
    gi = g[0]
    if gi and p and p["items"]:
        a, c = set(gi), set(p["items"])
        out.append(f"diff      : right {len(a & c)}, missing {len(a - c)} {sorted(a - c)[:6]}, extra {len(c - a)} {sorted(c - a)[:6]}")
    party, how = pid.identify(t)
    gparty = None
    if gi:
        from collections import Counter
        ps = Counter(rp.party_of(i) for i in gi if rp.party_of(i))
        gparty = ps.most_common(1)[0][0] if ps else None
    out.append(f"payer     : {party or '(none identified)'} via {how}" +
               (f"  | golden's party: {gparty or '(unknown: its items are not in the window ledger)'}"
                if gparty is None else f"  | golden's party: {gparty}{' (same)' if gparty == party else ' (DIFFERENT)'}") if gi else "")
    for label, pty in (("pipeline payer", party), ("golden party", gparty if gparty != party else None)):
        if not pty:
            continue
        cands = rp.candidates(pty, t)
        refs = rp.refs_visible(t, [c[0] for c in cands])
        out.append(f"open items of {label} {pty} at that moment: {len(cands)} ({sum(1 for c in cands if c[2])} with open <= payment x1.01); "
                   f"document number visible in the text for {refs}")
        amt = abs(float(b.amount))
        for iid, o, rng in sorted(cands, key=lambda c: abs(c[1] - amt))[:max_cands]:
            mark = ("G" if iid in gi else " ") + ("P" if (p and iid in p["items"]) else " ")
            out.append(f"   [{mark}] {iid}  open {o:>12.2f} {rp.items.at[iid, 'currency']}  issued {rp.items.at[iid, 'issue_date']}  "
                       f"doc {rp.items.at[iid, 'document_number']}{'' if rng else '   (> payment)'}")
        if len(cands) > max_cands:
            out.append(f"   ... {len(cands) - max_cands} more (closest to the payment amount shown first; G = in golden, P = in prediction)")
        elif cands:
            out.append("   (G = in golden, P = in prediction; closest to the payment amount first)")
    missing = [i for i in gi if i not in rp.items.index or rp.open_at(i, t) <= 0.005]
    if missing:
        out.append(f"golden items not open at that moment (duplicate / consumed / returned): {missing[:6]}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("txn", nargs="*", help="one or more bank_txn_id")
    ap.add_argument("--segment")
    ap.add_argument("--worst", type=int, metavar="N")
    ap.add_argument("--cands", type=int, default=15)
    ap.add_argument("--data", default=None)
    a = ap.parse_args()
    run = hr_lib.Run(a.run, a.data)
    if a.txn:
        ts = a.txn
    elif a.worst and a.segment:
        w = run.wrong_in(a.segment)
        if not w:
            sys.exit(f"no wrong line in segment {a.segment} (is the name right?)")
        ts = sorted(w, key=lambda t: -abs(float(run.bank.at[t, "amount"])))[:a.worst]
    else:
        sys.exit("give a bank_txn_id, or --segment S --worst N")
    for t in ts:
        if t not in run.golden.txns:
            sys.exit(f"{t} is not a bank line of this run's window")
    rp = hr_lib.Replay(run)
    pid = hr_lib.PayerId(run, rp)
    print("\n\n".join(show(run, rp, pid, t, a.cands) for t in ts))


if __name__ == "__main__":
    main()
