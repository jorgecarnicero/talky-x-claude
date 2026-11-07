"""Phase-shift report: compares two data directories (phase A = reference, phase B = new) with simple statistics.

    python solution/tools/shift_report.py ~/hackdata/data_dev ~/hackdata/data      # TRAIN-dev (A) vs LIVE (B)

Each directory must contain live/ (the phase: bank_transactions, items, advices) and train/ (its history: only
train/bank_transactions.csv.gz is read). Read-only, standard library only, < 2 minutes. Prints at most 20 lines:
one per measure with A, B and ALERTA when the change exceeds the threshold below.

Thresholds were calibrated on two pairs of "normal" phases, TRAIN-dev 2025-04..05 vs LIVE 2025-07..12 and TRAIN-dev vs
TRAIN 2024-11..12: each continuous threshold is ~1.5-2x the largest change seen between them, rounded (see TH). New or
vanished entities / accounts / currencies always alert (a fact, not a fluctuation); the line shows how many movements
they carry. Two measures are partly structural: "% counterparties without TRAIN history" is lower for a window inside
TRAIN (its history has months on both sides) than for LIVE (history only before), and the invoice->receipt delay grows
with the length of the phase (6 months of LIVE vs 2 of dev).
"""
import argparse
import csv
import gzip
import os
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter

# thresholds (absolute change, percentage points unless stated)
# observed |change| in the two normal pairs (dev vs LIVE ; dev vs TRAIN 2024-11..12) in brackets
TH = {
    "size_rel": 0.25,       # movements per month, relative change          [3 % ; 10 %]
    "no_hist": 10.0,        # % counterparties without history in TRAIN     [5.9 ; 0.1]
    "ref_valid": 5.0,       # % receipts whose reference matches a document [1.3 ; 0.5]
    "ref_absent": 5.0,      # % receipts with no document-like reference    [0.8 ; 0.4]
    "delay_med": 15,        # days, median invoice -> receipt               [9 ; 4]
    "delay_p90": 30,        # days, p90                                     [16 ; 10]
    "stmt": 7.0,            # share of one statement format (pp)            [3.2 ; 1.4]
    "remit": 7.0,           # share of one remittance-advice format (pp)    [2.8 ; 0.6]
    "pdf": 15.0,            # share of one PDF document type (pp)           [1.9 ; 10.0]
}


def _open(p):
    return gzip.open(p, "rt", encoding="utf-8-sig", newline="") if p.endswith(".gz") else \
        open(p, encoding="utf-8-sig", newline="")


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z ]", " ", s).split())


def norm_ref(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9/\-\.]{3,}")
DOC_LIKE = re.compile(r"(?=.*[A-Z])(?=(?:.*\d){4})")


def history(train):
    names, accounts, ccy = set(), set(), set()
    p = os.path.join(train, "bank_transactions.csv.gz")
    if os.path.exists(p):
        with _open(p) as fh:
            for r in csv.DictReader(fh):
                if r["counterparty_name"]:
                    names.add(norm_name(r["counterparty_name"]))
                accounts.add(r["account_id"])
                ccy.add(r["currency"])
    return names, accounts, ccy


def docs(live):
    """normalized document number -> (issue_date, is_AR)"""
    out = {}
    specs = [("open_items_at_start", "document_number", lambda r: r["item_type"].startswith("AR")),
             ("ar_invoices", "document_number", lambda r: True),
             ("ap_invoices", "supplier_invoice_number", lambda r: False),
             ("credit_notes", "document_number", lambda r: r.get("side", "").upper().startswith("AR"))]
    ccy = set()
    for f, col, is_ar in specs:
        p = os.path.join(live, f + ".csv.gz")
        if not os.path.exists(p):
            continue
        with _open(p) as fh:
            for r in csv.DictReader(fh):
                k = norm_ref(r.get(col) or "")
                if len(k) >= 4:
                    out.setdefault(k, (r.get("issue_date", ""), is_ar(r)))
                ccy.add(r.get("currency", ""))
    return out, ccy


def index_shares(path, col):
    c = Counter()
    if os.path.exists(path):
        with _open(path) as fh:
            for r in csv.DictReader(fh):
                c[r.get(col, "")] += 1
    return c


def days(a, b):
    from datetime import date
    try:
        return (date.fromisoformat(a) - date.fromisoformat(b)).days
    except ValueError:
        return None


def phase(d):
    live, train = os.path.join(d, "live"), os.path.join(d, "train")
    hn, hacc, hccy = history(train)
    dn, iccy = docs(live)
    ent, acc, ccy, months = Counter(), Counter(), Counter(), set()
    n = cp = cp_new = rec = valid = absent = 0
    delays, fmt = [], Counter()
    with _open(os.path.join(live, "bank_transactions.csv.gz")) as fh:
        for r in csv.DictReader(fh):
            n += 1
            ent[r["entity"]] += 1
            acc[r["account_id"]] += 1
            ccy[r["currency"]] += 1
            months.add(r["booking_date"][:7])
            fmt[r["statement_format"]] += 1
            if r["counterparty_name"]:
                cp += 1
                cp_new += norm_name(r["counterparty_name"]) not in hn
            if float(r["amount"] or 0) <= 0:
                continue
            rec += 1
            toks = [norm_ref(t) for f in ("ref2", "text", "remittance_info", "end_to_end_id")
                    for t in TOKEN.findall(r[f] or "")]
            hit = next((dn[t] for t in toks if t in dn), None)
            if hit:
                valid += 1
                if hit[1] and hit[0]:
                    dd = days(r["booking_date"], hit[0])
                    if dd is not None and 0 <= dd <= 730:
                        delays.append(dd)
            elif not any(DOC_LIKE.match(t) for t in toks):
                absent += 1
    delays.sort()
    p = lambda c: {k: 100 * v / max(1, sum(c.values())) for k, v in c.items()}  # noqa: E731
    return {
        "n": n, "months": len(months), "per_month": n / max(1, len(months)), "entities": ent, "accounts": acc,
        "acc_new_vs_train": set(acc) - hacc, "ccy": (set(ccy) | iccy) - {""}, "ccy_new_vs_train": set(ccy) - hccy,
        "ent_n": ent, "acc_n": acc, "ccy_n": ccy,
        "no_hist": 100 * cp_new / max(1, cp), "cp_share": 100 * cp / max(1, n),
        "ref_valid": 100 * valid / max(1, rec), "ref_absent": 100 * absent / max(1, rec),
        "delay_med": statistics.median(delays) if delays else None,
        "delay_p90": delays[int(0.9 * (len(delays) - 1))] if delays else None,
        "stmt": p(fmt),
        "remit": p(index_shares(os.path.join(live, "remittance_advices", "index.csv"), "format")),
        "pdf": p(index_shares(os.path.join(live, "pdfs", "index.csv"), "document_type")),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", help="reference phase dir (e.g. ~/hackdata/data_dev)")
    ap.add_argument("b", help="new phase dir (e.g. ~/hackdata/data)")
    x = ap.parse_args()
    t0 = time.time()
    A, B = (phase(os.path.expanduser(d)) for d in (x.a, x.b))
    L, n_alert = [], 0

    def line(name, va, vb, alert, extra=""):
        nonlocal n_alert
        n_alert += bool(alert)
        L.append(f"{'ALERTA' if alert else 'ok':<6} {name:<36} A={va!s:<10} B={vb!s:<10} {extra}")

    def lst(s, k=4):
        s = sorted(s)
        return ",".join(s[:k]) + (f" +{len(s) - k}" if len(s) > k else "") if s else "-"

    rel = B["per_month"] / A["per_month"] - 1
    line("tamaño (mov./mes; meses)", f"{A['per_month']:.0f};{A['months']}", f"{B['per_month']:.0f};{B['months']}",
         abs(rel) > TH["size_rel"], f"{rel:+.0%} (umbral ±{TH['size_rel']:.0%})")
    def w(s, cnt):  # movements of B carried by the new keys
        return f"{sum(cnt[k] for k in s)} mov. B"

    ea, eb = set(A["ent_n"]), set(B["ent_n"])
    line("entidades nuevas / desaparecidas", len(ea), len(eb), ea != eb,
         f"+[{lst(eb - ea)}] -[{lst(ea - eb)}] ({w(eb - ea, B['ent_n'])})")
    aa, ab = set(A["acc_n"]), set(B["acc_n"])
    line("cuentas nuevas / desaparecidas", len(aa), len(ab), aa != ab,
         f"+[{lst(ab - aa, 2)}] -[{lst(aa - ab, 2)}] ({w(ab - aa, B['acc_n'])})")
    line("cuentas sin historial en su TRAIN", len(A["acc_new_vs_train"]), len(B["acc_new_vs_train"]),
         bool(B["acc_new_vs_train"]), f"{lst(B['acc_new_vs_train'], 3)} ({w(B['acc_new_vs_train'], B['acc_n'])})")
    nc = (B["ccy"] - A["ccy"]) | B["ccy_new_vs_train"]
    line("divisas nuevas (frente a A o TRAIN)", len(A["ccy"]), len(B["ccy"]), bool(nc),
         f"[{lst(nc)}] ({w(nc, B['ccy_n'])})")
    for k, name, unit in (("no_hist", "% contrapartes sin historial TRAIN", "pp"),
                          ("ref_valid", "% cobros con ref. válida", "pp"),
                          ("ref_absent", "% cobros sin ref. (ausente)", "pp")):
        d = B[k] - A[k]
        line(name, f"{A[k]:.1f}", f"{B[k]:.1f}", abs(d) > TH[k], f"{d:+.1f} {unit} (umbral ±{TH[k]:g})")
    for k, name in (("delay_med", "retraso factura->cobro mediana (d)"), ("delay_p90", "retraso factura->cobro p90 (d)")):
        if A[k] is None or B[k] is None:
            line(name, A[k], B[k], True, "sin datos")
        else:
            d = B[k] - A[k]
            line(name, f"{A[k]:.0f}", f"{B[k]:.0f}", abs(d) > TH[k], f"{d:+.0f} d (umbral ±{TH[k]})")
    for k, name in (("stmt", "formatos de extracto"), ("remit", "formatos de remesa"), ("pdf", "tipos de PDF")):
        new = set(B[k]) - set(A[k])
        gone = set(A[k]) - set(B[k])
        worst = max(set(A[k]) | set(B[k]), key=lambda f: abs(B[k].get(f, 0) - A[k].get(f, 0)), default=None)
        dw = (B[k].get(worst, 0) - A[k].get(worst, 0)) if worst else 0
        line(f"{name} nuevos / cuota", len(A[k]), len(B[k]), bool(new) or abs(dw) > TH[k],
             f"+[{lst(new)}] -[{lst(gone)}] máx Δcuota {worst} {dw:+.1f} pp (umbral ±{TH[k]:g})")
    print(f"shift_report  A={x.a}  B={x.b}  (A {A['n']} mov., B {B['n']} mov.)")
    print("\n".join(L))
    print(f"{n_alert} alerta(s) en {len(L)} medidas  [{time.time() - t0:.0f} s]")
    sys.exit(1 if n_alert else 0)


if __name__ == "__main__":
    main()
