"""Document PDFs -> structured fields + the free-text clauses that exist only in the PDF (POLICIES §5.6-5.11).

    python solution/pdfs.py train/pdfs/US01/ARI_1082508553.pdf        # one PDF -> JSON
    python solution/pdfs.py --build                                   # parse every PDF of every phase (cached)
    python solution/pdfs.py --stats                                   # clause counts per document type

Requires PyMuPDF (`pip install pymupdf`, see requirements.txt). Field extraction (issuer, client, totals, lines) is
the team's original `extraer_factura_a_json`; clause parsing was added on top. The matcher (bankrec.py) only uses
`clauses` plus the index metadata (document type / number / entity / party).

Clauses (templated sentences, sometimes in the document language):
  instalments   "Payment terms: 3 instalments at 30/60/90 days (a / b / c CUR)."  -> amounts of each instalment
  retention     "Retention 10% (X CUR) withheld until final acceptance (dd/mm/yyyy). Amount payable at due date: Y"
  advance       "Less advance received against proforma PF25-..: A CUR. Net amount due B CUR."
  replaces      "This invoice cancels and replaces invoice X dated ..." / "anula y sustituye a la factura X"
  service_month "Maintenance and service fee for the month of January 2025."
  rebate        "Volume rebate Q1 2025 (...): to be offset against your next payment." | "will be paid to your bank account."
  cn_mode       supplier credit note: "deducted from your next payment" (offset) | "refunded by bank transfer" (refund)
  factor        "The receivable ... has been assigned to <FACTOR>. Payment is only discharging if made to it."
  new_iban      supplier invoice: "from dd/mm/yyyy our bank details change. New IBAN X"
                bank account notice: "from dd/mm/yyyy our payments will be made from account X (previously Y)"
"""
import argparse
import csv
import json
import os
import pickle
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CACHE = os.path.join(ROOT, "solution", "cache", "pdf_index_v2.pkl")  # v2: multilingual bank notices, parallel build
NO_CLAUSE_TYPES = {"proforma"}  # never carry a clause: indexed without opening the file
PHASES = ("train", "live", "final", "sample")


def _fitz():
    try:
        import pymupdf  # noqa
        return pymupdf
    except ImportError:  # older PyMuPDF
        import fitz  # noqa
        return fitz


# ----------------------------------------------------------------------------- numbers / dates
def parse_float(val_str):
    """'4,286.95' / '4.286,95' / '26 555,24' / '143.237.17' -> float"""
    s = val_str.strip().replace(" ", " ").replace(" ", "")
    if re.search(r",\d{2}$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    if s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1]
    return float(s)


def cents(val_str):
    return int(round(parse_float(val_str) * 100))


def iso(dmy):
    d, m, y = dmy.split("/")
    return f"{y}-{m}-{d}"


NUM = r"\d[\d.,  ]*\d"
MONTHS = {}
for i, names in enumerate([
        "january enero janvier januar gennaio janeiro", "february febrero fevrier février februar febbraio fevereiro",
        "march marzo mars marz märz marco março", "april abril avril aprile", "may mayo mai maggio maio",
        "june junio juin juni giugno junho", "july julio juillet juli luglio julho",
        "august agosto aout août", "september septiembre septembre settembre setembro",
        "october octubre octobre oktober ottobre outubro", "november noviembre novembre novembro",
        "december diciembre decembre décembre dezember dicembre dezembro"], 1):
    for n in names.split():
        MONTHS[n] = i


# ----------------------------------------------------------------------------- clauses
def parse_clauses(text):
    t = re.sub(r"\s+", " ", text)
    c = {}
    m = re.search(r"(\d+) instalments at ([\d/]+) days \(([^)]*)\)", t, re.I)
    if m:
        parts = [p.strip() for p in m.group(3).split("/")]
        cur = re.search(r"([A-Z]{3})\s*$", parts[-1])
        amts = [cents(re.sub(r"[A-Z]{3}", "", p)) for p in parts]
        c["instalments"] = {"n": int(m.group(1)), "days": [int(x) for x in m.group(2).split("/")], "amounts": amts,
                            "currency": cur.group(1) if cur else None}
    else:
        m = re.search(r"(\d+) (?:vencimientos|plazos|échéances|Raten|rate|parcelas)", t, re.I)
        if m:
            c["instalments"] = {"n": int(m.group(1)), "days": [], "amounts": [], "currency": None}
    m = re.search(r"Retention (\d+(?:[.,]\d+)?)\s?% \((" + NUM + r") ([A-Z]{3})\) withheld until final acceptance"
                  r" \((\d{2}/\d{2}/\d{4})\)\.? Amount payable at due date: (" + NUM + r") ([A-Z]{3})", t)
    if m:
        c["retention"] = {"pct": float(m.group(1).replace(",", ".")), "retained": cents(m.group(2)),
                          "until": iso(m.group(4)), "payable": cents(m.group(5)), "currency": m.group(3)}
    m = re.search(r"Less advance received against proforma (\S+?):? (" + NUM + r") ([A-Z]{3})\.? Net amount due (" +
                  NUM + r") ([A-Z]{3})", t)
    if m:
        c["advance"] = {"proforma": m.group(1), "advance": cents(m.group(2)), "net_due": cents(m.group(4)),
                        "currency": m.group(3)}
    m = re.search(r"(?:cancels and replaces invoice|anula y sustituye a la factura|annule et remplace la facture|"
                  r"storniert und ersetzt (?:die )?Rechnung|annulla e sostituisce la fattura|anula e substitui a fatura)"
                  r"\s+(\S+?)[,.]?\s+(?:dated|de|du|vom|del|da)\s+(\d{2}/\d{2}/\d{4})", t, re.I)
    if m:
        c["replaces"] = {"document_number": m.group(1), "date": iso(m.group(2))}
    m = re.search(r"(?:for the month of|correspondiente al mes de|du mois de|für den Monat|del mese di|referente ao mês de)"
                  r"\s+([A-Za-zéèûäçã]+)\s+(\d{4})", t, re.I)
    if m and m.group(1).lower() in MONTHS:
        c["service_month"] = f"{m.group(2)}-{MONTHS[m.group(1).lower()]:02d}"
    m = re.search(r"Volume rebate (Q\d \d{4})[^:]*:\s*(to be offset|will be paid)", t, re.I)
    if m:
        c["rebate"] = {"quarter": m.group(1), "mode": "offset" if "offset" in m.group(2).lower() else "paid_out"}
    if re.search(r"deducted from your next payment", t, re.I):
        c["cn_mode"] = "offset"
    elif re.search(r"refunded by bank transfer", t, re.I):
        c["cn_mode"] = "refund"
    m = re.search(r"has been assigned to (.+?)\. Payment is only", t)
    if m:
        c["factor"] = m.group(1).strip()
    m = re.search(r"from (\d{2}/\d{2}/\d{4}) our bank details change\. New IBAN ([A-Z]{2}\d{2}[A-Z0-9]{8,30})", t)
    if m:
        c["new_iban"] = {"from": iso(m.group(1)), "iban": m.group(2), "previous": None}
    m = re.search(r"from (\d{2}/\d{2}/\d{4}) our payments will be made from account ([A-Z]{2}\d{2}[A-Z0-9]{8,30})"
                  r"(?: \(previously ([A-Z]{2}\d{2}[A-Z0-9]{8,30})\))?", t)
    if m:
        c["new_iban"] = {"from": iso(m.group(1)), "iban": m.group(2), "previous": m.group(3)}
    if "new_iban" not in c:
        # same notice in the customer's language: "a partir del 15/07/2024 nuestros pagos ... desde la cuenta ES54...,
        # en sustitución de la anterior ES15..." / "à partir du ..." / "ab dem ..." / "dal ..." / "a partir de ..."
        m = re.search(r"(?:a partir del?|à partir du|a partir de|ab dem|ab|dal|vanaf|od)\s+(\d{2}/\d{2}/\d{4})(.{0,260}?)"
                      r"\b([A-Z]{2}\d{2}[A-Z0-9]{8,30})\b(?:.{0,120}?\b([A-Z]{2}\d{2}[A-Z0-9]{8,30})\b)?", t, re.I)
        if m and re.search(r"pag|paie|paiement|zahl|pagament|betal|płat|plat|cuenta|compte|konto|conto|conta|rekening",
                           m.group(2), re.I):
            c["new_iban"] = {"from": iso(m.group(1)), "iban": m.group(3), "previous": m.group(4)}
    if "new_iban" in c and "bank details change" not in t:  # customer notice: payments vs direct-debit account
        c["new_iban"]["kind"] = "direct_debit" if re.search(r"recibos domiciliados|pr[ée]l[èe]vement|lastschrift|"
                                                            r"direct debit|addebit", t, re.I) else "payments"
    return c


# ----------------------------------------------------------------------------- the team's field extractor
def extraer_factura_a_json(pdf_path, texto=None):
    """Original field extractor (header, client, totals, lines) + `clauses`. English layout labels; fields that are not
    found stay None (other languages)."""
    if texto is None:
        doc = _fitz().open(pdf_path)
        texto = "\n".join([page.get_text("text") for page in doc])
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]

    emisor, emisor_tax_id, cliente, cliente_direccion, cliente_vat = "", None, "", None, None
    if "Bill to" in lineas:
        idx_bill = lineas.index("Bill to")
        header_lines = [l for l in lineas[:idx_bill] if l.upper() != "INVOICE"]
        if header_lines:
            emisor = header_lines[0]
        if len(header_lines) > 1:
            emisor_tax_id = header_lines[1].replace("VAT", "").strip()
        idx_desc = lineas.index("Description") if "Description" in lineas else idx_bill + 4
        client_lines = lineas[idx_bill + 1: idx_desc]
        if client_lines:
            cliente = client_lines[0]
            addr_parts = []
            for cl in client_lines[1:]:
                if cl.upper().startswith("VAT"):
                    cliente_vat = cl.replace("VAT", "").strip()
                else:
                    addr_parts.append(cl)
            if addr_parts:
                cliente_direccion = ", ".join(addr_parts)
    else:
        header_lines = [l for l in lineas if l.upper() != "INVOICE"]
        emisor = header_lines[0] if header_lines else ""

    inv_no = re.search(r"No\.\s*\n?\s*([A-Z0-9][\w\-/]+)", texto)
    fecha = re.search(r"Date\s*\n?\s*(\d{2}/\d{2}/\d{4})", texto)
    due_date = re.search(r"Due date\s*\n?\s*(\d{2}/\d{2}/\d{4})", texto)
    po = re.search(r"Your PO\s*\n?\s*([\w\-/]+)", texto)
    terms = re.search(r"(?:Terms|Forma de pago|Zahlungsbedingungen|Pagamento|Condi\w+):\s*([^\n]+)", texto)
    subtotal_match = re.search(r"Subtotal\s*\n?\s*([\d,\.]+[.,]\d{2})\s*([A-Z]{3})?", texto)
    tax_match = re.search(r"Tax\s*\n?\s*([\d,\.]+[.,]\d{2})\s*([A-Z]{3})?", texto)
    total_match = re.search(r"(?:TOTAL|GESAMT|TOTALE)\s*\n?\s*([\d,\. ]+[.,]\d{2})\s*([A-Z]{3})?", texto)

    items = []
    if "Amount" in lineas:
        idx = lineas.index("Amount") + 1
        while idx + 3 < len(lineas):
            if lineas[idx].startswith(("Terms:", "Documento", "INVOICE", "Subtotal", "No.")):
                break
            qty_str, unit_str, amount_str = lineas[idx + 1], lineas[idx + 2], lineas[idx + 3]
            if re.match(r"^\d+$", qty_str) and re.match(r"^[\d,\.]+\.\d{2}$", unit_str):
                try:
                    items.append({"description": lineas[idx], "qty": int(qty_str), "unit_price": parse_float(unit_str),
                                  "amount": parse_float(amount_str)})
                except ValueError:
                    pass
                idx += 4
            else:
                idx += 1

    def num(m):
        try:
            return parse_float(m.group(1)) if m else None
        except ValueError:
            return None

    return {
        "file_name": Path(pdf_path).name,
        "issuer_company": emisor, "issuer_tax_id": emisor_tax_id,
        "client_company": cliente, "client_address": cliente_direccion, "client_vat": cliente_vat,
        "invoice_number": inv_no.group(1) if inv_no else None,
        "date": fecha.group(1) if fecha else None, "due_date": due_date.group(1) if due_date else None,
        "po_number": po.group(1) if po else None,
        "payment_terms": terms.group(1).strip() if terms else None,
        "currency": total_match.group(2) if (total_match and total_match.group(2)) else None,
        "subtotal": num(subtotal_match), "tax": num(tax_match), "total": num(total_match),
        "items": items,
        "clauses": parse_clauses(texto),
    }


# ----------------------------------------------------------------------------- corpus index (cached)
def _parse_file(path):
    """worker: whole-document text -> clauses (module level so a process pool can pickle it). Clauses always sit in
    the same band of a single page (y = 15-50 % of the height), but clipping to that zone saved no time (text
    extraction is ~2.5 ms; the cost is cold file reads), so the full text is parsed for robustness."""
    try:
        doc = _fitz().open(path)
        return parse_clauses("\n".join(p.get_text("text") for p in doc))
    except Exception as e:  # noqa
        return {"error": type(e).__name__}


def load_index(root=ROOT, build=True, verbose=True):
    """{(phase, file): {document_type, document_number, entity, party_id, document_date, clauses}} for every PDF present.
    Only the clauses are kept (small); incremental: new PDFs (e.g. final/) are parsed on the next call."""
    idx = {}
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as fh:
            idx = pickle.load(fh)
    if not build:
        return idx
    fz = None
    todo = []
    for ph in PHASES:
        ip = os.path.join(root, ph, "pdfs", "index.csv")
        if not os.path.exists(ip):
            continue
        for r in csv.DictReader(open(ip, encoding="utf-8")):
            key = (ph, r["file"])
            if key in idx:
                continue
            path = os.path.join(root, ph, "pdfs", r["file"])
            if os.path.exists(path):
                todo.append((key, path, r))
    if todo:
        t0 = time.time()
        skip = [x for x in todo if x[2]["document_type"] in NO_CLAUSE_TYPES]
        work = [x for x in todo if x[2]["document_type"] not in NO_CLAUSE_TYPES]
        paths = [path for _, path, _ in work]
        if len(paths) > 200:  # cold reads are I/O bound: a process pool is ~6x faster than sequential
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 2)) as ex:
                results = []
                for n, cl in enumerate(ex.map(_parse_file, paths, chunksize=64), 1):
                    results.append(cl)
                    if verbose and n % 10000 == 0:
                        print(f"  pdfs: {n}/{len(paths)} parsed ({time.time() - t0:.0f}s)", flush=True)
        else:
            results = [_parse_file(pth) for pth in paths]
        for (key, path, r), cl in list(zip(work, results)) + [(x, {}) for x in skip]:
            idx[key] = {"document_type": r["document_type"], "document_number": r["document_number"],
                        "entity": r["entity"], "party_id": r["party_id"], "document_date": r["document_date"],
                        "clauses": cl}
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "wb") as fh:
            pickle.dump(idx, fh)
        os.replace(tmp, CACHE)
        if verbose:
            print(f"  pdfs: {len(todo)} new PDFs indexed in {time.time() - t0:.0f}s, index size {len(idx)}", flush=True)
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.pdf:
        print(json.dumps(extraer_factura_a_json(a.pdf), indent=2, ensure_ascii=False))
    if a.build or a.stats:
        idx = load_index()
        if a.stats:
            c = Counter()
            for v in idx.values():
                c[(v["document_type"], "_total")] += 1
                for k in v["clauses"]:
                    c[(v["document_type"], k)] += 1
            for (dt, k), n in sorted(c.items()):
                print(f"{dt:22s} {k:15s} {n:7d}")


if __name__ == "__main__":
    sys.exit(main())
