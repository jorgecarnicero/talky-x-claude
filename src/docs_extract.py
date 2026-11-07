"""Offline extraction of remittance advices + document PDFs into parquet caches.

    .venv/bin/python src/docs_extract.py --phase train|live|final [--only ra|pdf] [--workers 3]

Outputs (under CACHE = ../cache):
    docs_ra_<phase>.parquet   one row per remittance-advice line (header fields repeated)
    docs_pdf_<phase>.parquet  one row per document PDF with parsed free-text clauses

Data files are untrusted: they are only parsed (text extraction), never executed. Unknown layouts are logged and
skipped (an advice with no parsable line still yields one header row with n_lines=0).
"""
import argparse
import csv
import io
import os
import re
import sys
import time
from multiprocessing import Pool

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from data import CACHE, DEFAULT_DATA  # noqa: E402
import docs_clauses  # noqa: E402

# ----------------------------------------------------------------------------- generic token helpers
AMT_RE = re.compile(r"^[-−]?\(?\d{1,3}(?:[.,\s' ]\d{3})*(?:[.,]\d{1,2})\)?-?$|^[-−]?\d+(?:[.,]\d{1,2})$")
DATE_RES = [
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})"), "ymd"),
    (re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$"), "dmy4"),
    (re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{2})$"), "dmy2"),
]
CURRENCIES = {"EUR", "USD", "GBP", "AUD", "CAD", "CHF", "PLN", "MXN", "BRL", "CNY", "MAD", "ZAR", "JPY", "SEK",
              "NOK", "DKK", "CZK", "HUF", "RMB"}


def parse_amount(s):
    """'1.501,63' / '1,501.63' / '-91.40' / '1 234,56' / 4981.1 -> float, else None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace("−", "-").replace(" ", " ")
    if not s or not AMT_RE.match(s):
        return None
    neg = s.startswith("-") or s.endswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("-()")
    m = re.search(r"[.,](\d{1,2})$", s)
    if m:
        intpart, dec = s[:m.start()], m.group(1)
    else:
        intpart, dec = s, "0"
    intpart = re.sub(r"[.,\s']", "", intpart)
    if not intpart.isdigit():
        return None
    v = float(intpart + "." + dec)
    return -v if neg else v


def parse_date(s, us=False):
    if s is None:
        return None
    if hasattr(s, "strftime"):
        return s.strftime("%Y-%m-%d")
    s = str(s).strip()
    for rx, kind in DATE_RES:
        m = rx.match(s)
        if not m:
            continue
        if kind == "ymd":
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if kind == "dmy2":
            y = "20" + y
        d, mo = (b, a) if us else (a, b)
        if mo > 12 and d <= 12:
            d, mo = mo, d
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            return None
        return f"{y}-{mo:02d}-{d:02d}"
    return None


def is_doc_token(s):
    """Doc-number-like: has >=4 digits, mostly alnum/-/ , not a pure amount or date."""
    if s is None:
        return False
    s = str(s).strip()
    if not s or len(s) > 30 or " " in s:
        return False
    if parse_date(s) is not None:
        return False
    digits = sum(c.isdigit() for c in s)
    if digits < 4:
        return False
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9\-/_.]*$", s):
        return False
    if re.match(r"^-?\d{1,3}([.,]\d{3})*[.,]\d{2}$", s) or re.match(r"^-?\d+[.,]\d{2}$", s):
        return False
    return True


# ----------------------------------------------------------------------------- reading files into rows
def pdf_lines(path):
    """Reconstruct visual lines of a PDF (words grouped by baseline)."""
    import fitz
    out = []
    with fitz.open(path) as doc:
        for page in doc:
            words = page.get_text("words")  # x0,y0,x1,y1,word,block,line,wno
            words.sort(key=lambda w: (round(w[3]), w[0]))
            cur, cur_y = [], None
            for w in words:
                y = w[3]
                if cur_y is None or abs(y - cur_y) <= 2.5:
                    cur.append(w)
                    cur_y = y if cur_y is None else cur_y
                else:
                    out.append(_join_words(cur))
                    cur, cur_y = [w], y
            if cur:
                out.append(_join_words(cur))
    return out


def _join_words(ws):
    ws = sorted(ws, key=lambda w: w[0])
    parts, last_x1 = [], None
    for w in ws:
        if last_x1 is not None and w[0] - last_x1 > 8:
            parts.append("  ")
        elif last_x1 is not None:
            parts.append(" ")
        parts.append(w[4])
        last_x1 = w[2]
    return "".join(parts)


def pdf_text(path):
    import fitz
    with fitz.open(path) as doc:
        return "\n".join(p.get_text() for p in doc)


def read_rows(path):
    """Return (rows, kind): rows = list of list of cells (strings or numbers)."""
    ext = path.rsplit(".", 1)[-1].lower()
    if ext == "xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = []
        for ws in wb.worksheets:
            for r in ws.iter_rows(values_only=True):
                cells = [c for c in r]
                while cells and (cells[-1] is None or cells[-1] == ""):
                    cells.pop()
                if cells:
                    rows.append(["" if c is None else c for c in cells])
        wb.close()
        return rows, "table"
    if ext == "csv":
        raw = open(path, "rb").read()
        txt = raw.decode("utf-8", errors="replace").lstrip("﻿")
        first = txt.splitlines()[0] if txt else ""
        delim = max([";", ",", "\t", "|"], key=lambda d: first.count(d))
        rows = [r for r in csv.reader(io.StringIO(txt), delimiter=delim) if any(c.strip() for c in r)]
        return rows, "table"
    if ext == "pdf":
        lines = pdf_lines(path)
    else:
        raw = open(path, "rb").read()
        try:
            txt = raw.decode("utf-8")
        except UnicodeDecodeError:
            txt = raw.decode("latin-1")
        lines = txt.splitlines()
    rows = []
    for ln in lines:
        if not ln.strip():
            continue
        cells = [c for c in re.split(r"\s{2,}|\t", ln.strip()) if c != ""]
        rows.append(cells)
    return rows, "text"


# ----------------------------------------------------------------------------- remittance parsing
HDR_MAP = [
    ("voucher", r"voucher|beleg|justificante|asiento|pi[eè]ce|vchr"),
    ("doc", r"^doc|document|invoice|factura|rechnung|facture|fattura|fatura|^inv|^n[ºo°]|^ref"),
    ("po", r"\bpo\b|order|pedido|bestell|commande|ordine|^oc$"),
    ("date", r"date|fecha|datum|data"),
    ("ded", r"deduc|discount|abzug|descuento|retenue|skonto|ajuste|adjust"),
    ("reason", r"reason|motivo|grund|motif|causale|comment|remark|concept"),
    ("paid", r"paid|net|pagado|bezahlt|pay[eé]|pagato|pago"),
    ("gross", r"gross|amount|importe|betrag|montant|importo|valor|bruto|brutto|total|sum"),
]


def classify_header(cells):
    roles = {}
    hits = 0
    for i, c in enumerate(cells):
        s = str(c).strip().lower()
        if not s or parse_amount(s) is not None:
            continue
        for role, rx in HDR_MAP:
            if re.search(rx, s) and role not in roles:
                roles[role] = i
                hits += 1
                break
    if hits >= 3 and ("paid" in roles or "gross" in roles) and ("doc" in roles or "voucher" in roles or "po" in roles):
        return roles
    return None


def header_fields(rows):
    """Payment reference, date, currency, total from header-ish rows (any layout)."""
    ref = pdate = cur = total = None
    flat_lines = [" ".join(str(c) for c in r) for r in rows]
    for i, r in enumerate(rows):
        cells = [str(c).strip() for c in r]
        low = [c.lower() for c in cells]
        joined = " ".join(cells)
        jl = joined.lower()
        # key/value pairs in the same row: payment_ref;X;payment_date;Y;currency;Z
        for j, c in enumerate(low):
            nxt = cells[j + 1] if j + 1 < len(cells) else ""
            if re.search(r"payment[_ ]?ref|referen|zahlungsref|ref\.? pago|^ref\.?$|payment advice|aviso", c) and nxt:
                if ref is None and re.search(r"\d{3}", nxt):
                    ref = nxt.split()[0]
                # 'Payment advice', ref, date
                if j + 2 < len(cells) and parse_date(cells[j + 2]):
                    pdate = pdate or parse_date(cells[j + 2])
            if re.search(r"date|fecha|datum", c) and nxt and parse_date(nxt):
                pdate = pdate or parse_date(nxt)
            if re.search(r"currency|moneda|w[aä]hrung|devise", c) and nxt.upper() in CURRENCIES:
                cur = cur or nxt.upper()
        m = re.search(r"(?:payment ref\.?|ref\.? de pago|referencia|zahlungsreferenz|r[ée]f[ée]rence)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{3,})", joined, re.I)
        if m and ref is None:
            ref = m.group(1)
        m = re.match(r"subject:\s*(.*)$", joined, re.I)
        if m and ref is None:
            toks = re.findall(r"[A-Z0-9][A-Z0-9\-/]*\d{3,}[A-Z0-9\-/]*", m.group(1), re.I)
            if toks:
                ref = toks[-1]
        # amount sentences: 'payment of 18,368.32 EUR on 07/01/2024', 'importe de 12.135,33 EUR con fecha 01/07/2024'
        m = re.search(r"([\d][\d.,\s']*\d)\s*([A-Z]{3})\b(?:\s+(?:on|con fecha|am|le|il|em|en)\s+(\S+))?", joined)
        if m and m.group(2) in CURRENCIES and re.search(r"payment|pago|importe|zahlung|paiement|pagamento|amount|transfer|virement|bonifico", jl):
            v = parse_amount(m.group(1).replace(" ", ""))
            if v is not None and total is None:
                total, cur = v, cur or m.group(2)
                if m.group(3):
                    us = bool(re.search(r"\bon\b", jl))
                    pdate = pdate or parse_date(m.group(3).rstrip(":,."), us=us)
        if re.match(r"^(amount|importe|betrag|montant|importo|total amount)\b", jl) and total is None:
            for c in cells[1:] + joined.split()[1:]:
                v = parse_amount(c.split()[0]) if c else None
                if v is not None:
                    total = v
                    cc = re.findall(r"\b([A-Z]{3})\b", joined)
                    for x in cc:
                        if x in CURRENCIES:
                            cur = cur or x
                    break
        if re.match(r"^(date|fecha|datum)\b", jl) and pdate is None:
            for c in joined.split()[1:]:
                if parse_date(c):
                    pdate = parse_date(c)
                    break
        if low and low[0] == "payment ref." and len(cells) == 1 and i + 1 < len(rows):
            pass
    # vertical layout (label on one line, value on next): handled by pdf line-joining normally; fallback here
    for i, ln in enumerate(flat_lines[:-1]):
        l = ln.strip().lower()
        nxt = flat_lines[i + 1].strip()
        if ref is None and re.match(r"^(payment ref\.?|referencia|reference)$", l):
            ref = nxt.split()[0] if nxt else None
        if total is None and re.match(r"^(amount|importe)$", l):
            total = parse_amount(nxt.split()[0]) if nxt else None
            cc = [x for x in re.findall(r"\b([A-Z]{3})\b", nxt) if x in CURRENCIES]
            cur = cur or (cc[0] if cc else None)
        if pdate is None and re.match(r"^(date|fecha)$", l):
            pdate = parse_date(nxt.split()[0]) if nxt else None
    return ref, pdate, cur, total


def parse_table(rows):
    lines, total_row = [], None
    roles = None
    for r in rows:
        if roles is None:
            roles = classify_header(r)
            continue
        cells = list(r)
        first = str(cells[0]).strip().upper() if cells else ""
        if first.startswith("TOTAL") or first in ("SUMME", "SUMA"):
            amts = [parse_amount(c) for c in cells[1:]]
            amts = [a for a in amts if a is not None]
            if amts:
                total_row = amts[-1] if "paid" in roles and len(cells) > roles["paid"] and parse_amount(cells[roles["paid"]]) is not None else amts[0]
            continue

        def get(role):
            i = roles.get(role)
            return cells[i] if i is not None and i < len(cells) else None
        gross, ded, paid = parse_amount(get("gross")), parse_amount(get("ded")), parse_amount(get("paid"))
        if gross is None and paid is None:
            # maybe a new header further down
            nr = classify_header(r)
            if nr:
                roles = nr
            continue
        if paid is None:
            paid = gross + (ded or 0.0)
        if gross is None:
            gross = paid - (ded or 0.0)
        if ded is None:
            ded = round(paid - gross, 2)
        doc = get("doc")
        lines.append({"doc": str(doc).strip() if doc not in (None, "") else "",
                      "voucher": str(get("voucher") or "").strip(),
                      "po": str(get("po") or "").strip(),
                      "doc_date": parse_date(get("date")) or "",
                      "gross": gross, "deduction": ded, "paid": paid,
                      "reason": str(get("reason") or "").strip()})
    return lines, total_row, roles is not None


def parse_text_lines(rows):
    """Free-text advices (e-mail, PDF): each line starting with a doc-like token followed by amounts."""
    lines, total_row = [], None
    for r in rows:
        toks = []
        for t in " ".join(str(c) for c in r).split():
            m = re.match(r"^(-?[\d.,]+\d)(-[\d.,]+\d)$", t)  # '111,351.76-11,135.17' (columns glued)
            if m and parse_amount(m.group(1)) is not None and parse_amount(m.group(2)) is not None:
                toks.extend([m.group(1), m.group(2)])
            else:
                toks.append(t)
        if not toks:
            continue
        if toks[0].upper().startswith("TOTAL"):
            a = [parse_amount(t) for t in toks[1:]]
            a = [x for x in a if x is not None]
            if a:
                total_row = a[-1]
            continue
        if not is_doc_token(toks[0]):
            continue
        doc = toks[0]
        date = ""
        amts, words, extra_docs = [], [], []
        for t in toks[1:]:
            d = parse_date(t)
            if d and not date and not amts:
                date = d
                continue
            a = parse_amount(t)
            if a is not None:
                amts.append(a)
                continue
            if is_doc_token(t) and not amts:
                extra_docs.append(t)
                continue
            words.append(t)
        if not amts:
            continue
        if len(amts) == 1:
            gross, ded, paid = amts[0], 0.0, amts[0]
        elif len(amts) == 2:
            if amts[1] < 0 and amts[0] > 0:
                gross, ded = amts[0], amts[1]
                paid = round(gross + ded, 2)
            else:
                gross, paid = amts[0], amts[1]
                ded = round(paid - gross, 2)
        else:
            gross, ded, paid = amts[0], amts[1], amts[2]
            if abs(gross + ded - paid) > 0.02 and abs(gross - ded - paid) < 0.02:
                ded = -ded
        lines.append({"doc": doc, "voucher": "", "po": extra_docs[0] if extra_docs else "", "doc_date": date,
                      "gross": gross, "deduction": ded, "paid": paid, "reason": " ".join(words)})
    return lines, total_row


def parse_ra(path):
    rows, kind = read_rows(path)
    ref, pdate, cur, total = header_fields(rows)
    lines, total_row, ok = ([], None, False)
    if kind == "table":
        lines, total_row, ok = parse_table(rows)
    if not lines:
        lines, total_row = parse_text_lines(rows)
    if not lines and kind == "text":
        lines, total_row, ok = parse_table(rows)
    if total is None:
        total = total_row
    return {"payment_ref": ref or "", "pay_date": pdate or "", "currency": cur or "", "total": total,
            "total_row": total_row, "lines": lines}


def _ra_worker(args):
    root, rel, meta = args
    path = os.path.join(root, rel)
    try:
        r = parse_ra(path)
        err = ""
    except Exception as e:  # unknown layout / broken file: log and skip
        r = {"payment_ref": "", "pay_date": "", "currency": "", "total": None, "total_row": None, "lines": []}
        err = f"{type(e).__name__}: {e}"[:200]
    base = dict(meta)
    base.update({k: r[k] for k in ("payment_ref", "pay_date", "currency", "total", "total_row")})
    base["n_lines"] = len(r["lines"])
    base["error"] = err
    out = []
    if not r["lines"]:
        out.append(dict(base, line_no=-1, doc="", voucher="", po="", doc_date="", gross=None, deduction=None,
                        paid=None, reason=""))
    for i, ln in enumerate(r["lines"]):
        out.append(dict(base, line_no=i, **ln))
    return out


# ----------------------------------------------------------------------------- PDF documents
def _pdf_worker(args):
    root, rel, meta = args
    path = os.path.join(root, rel)
    try:
        txt = pdf_text(path)
        err = ""
    except Exception as e:
        txt, err = "", f"{type(e).__name__}: {e}"[:200]
    rec = dict(meta)
    rec["error"] = err
    rec.update(docs_clauses.parse_clauses(txt, meta.get("document_type", "")))
    rec["text"] = txt
    return rec


# ----------------------------------------------------------------------------- driver
def phase_dir(data, phase):
    return os.path.join(data, phase)


def run_ra(data, phase, workers):
    root = os.path.join(phase_dir(data, phase), "remittance_advices")
    idx = pd.read_csv(os.path.join(root, "index.csv"), dtype=str, keep_default_na=False)
    idx = idx[idx.file.map(lambda f: os.path.exists(os.path.join(root, f)))]
    jobs = [(root, r.file, {"file": r.file, "entity": r.entity, "payer_name": r.payer_name,
                            "received_date": r.received_date, "format": r.format}) for r in idx.itertuples()]
    t0 = time.time()
    rows = []
    with Pool(workers) as p:
        for out in p.imap_unordered(_ra_worker, jobs, chunksize=32):
            rows.extend(out)
    df = pd.DataFrame(rows)
    for c in ("total", "total_row", "gross", "deduction", "paid"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in ("doc", "voucher", "po", "reason", "payment_ref", "doc_date", "pay_date", "currency", "error"):
        df[c] = df[c].fillna("").astype(str)
    out = os.path.join(CACHE, f"docs_ra_{phase}.parquet")
    df.to_parquet(out, index=False)
    hdr = df.drop_duplicates("file")
    print(f"RA {phase}: {len(hdr)} advices, {int((hdr.n_lines > 0).sum())} with lines, {len(df)} rows, "
          f"{int((hdr.error != '').sum())} errors, {int((hdr.payment_ref == '').sum())} no-ref, "
          f"{int(hdr.total.isna().sum())} no-total  [{time.time()-t0:.0f}s] -> {out}", flush=True)
    bad = hdr[(hdr.n_lines == 0) | (hdr.error != "")]
    for r in bad.head(10).itertuples():
        print("   skipped:", r.file, r.error)
    return df


def run_pdf(data, phase, workers):
    root = os.path.join(phase_dir(data, phase), "pdfs")
    idx = pd.read_csv(os.path.join(root, "index.csv"), dtype=str, keep_default_na=False)
    idx = idx[idx.file.map(lambda f: os.path.exists(os.path.join(root, f)))]
    jobs = [(root, r.file, {"file": r.file, "document_type": r.document_type, "document_number": r.document_number,
                            "entity": r.entity, "party_id": r.party_id, "document_date": r.document_date})
            for r in idx.itertuples()]
    t0 = time.time()
    with Pool(workers) as p:
        recs = list(p.imap_unordered(_pdf_worker, jobs, chunksize=64))
    df = pd.DataFrame(recs)
    out = os.path.join(CACHE, f"docs_pdf_{phase}.parquet")
    df.to_parquet(out, index=False)
    print(f"PDF {phase}: {len(df)} docs, {int((df.error != '').sum())} errors  [{time.time()-t0:.0f}s] -> {out}",
          flush=True)
    for c in docs_clauses.CLAUSE_COLS:
        if c in df:
            s = df[c]
            n = int(s.notna().sum() if s.dtype != bool else s.sum())
            if s.dtype == object:
                n = int((s.fillna("") != "").sum())
            print(f"   {c:22s} {n}")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--phase", required=True, choices=["sample", "train", "live", "final"])
    ap.add_argument("--only", choices=["ra", "pdf"])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true", help="re-extract even if the cache exists (train/live only skip)")
    a = ap.parse_args()
    import guards
    guards.check_all(a.data, a.phase)
    os.makedirs(CACHE, exist_ok=True)
    for kind, fn in (("ra", run_ra), ("pdf", run_pdf)):
        if a.only not in (None, kind):
            continue
        out = os.path.join(CACHE, f"docs_{kind}_{a.phase}.parquet")
        # train/live are immutable: reuse their cache. final/sample are always re-extracted (no stale partial cache).
        if os.path.exists(out) and a.phase in ("train", "live") and not a.force:
            print(f"{kind} {a.phase}: cache exists, skipping ({out})")
            continue
        fn(a.data, a.phase, a.workers)


if __name__ == "__main__":
    main()
