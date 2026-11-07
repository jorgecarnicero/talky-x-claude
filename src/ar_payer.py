"""Extract the ordering-party name from bank texts (N43 / MT940 / camt). Generic format patterns, no party names."""
import re

PATTERNS = [
    r"/ORDP/([^/]+)/",
    r"/NAME/([^/]+)/",
    r"/ORG/([^/]+?)\s*/",
    r"/DE ([^/]+?) /MOTIF",
    r"BONIFICO DA (.+?) CAUSALE",
    r"PIX RECEBIDO (.+?)(?: PAYMENT| UEBERWEISUNG| PAG |$)",
    r"LIQUIDACAO BOLETO [\d\-]+ (.+?)(?: PAG |$)",
    r"PRZELEW PRZYCH\. (.+?) TYTUL",
    r"ACH CREDIT (.+?) CCD",
    r"DIRECT CREDIT (.+?)(?:  | INV| PAYMENT|$)",
    r"TT IN (.+?)(?: INV| SERVICE| PAYMENT|$)",
    r"^FP (.+?)(?: PAYMENT| INV|$)",
    r"TRANSFERENCIAS (.+?)(?: \||$)",
    r"TRANSFERENCIA DE (.+?)(?: \| CONCEPTO|$)",
    r"\d{8}-(.+?)(?: \||$)",
    r"ORD\. (.+?)(?: FRA | FR$| \||$)",
]
_C = [re.compile(p) for p in PATTERNS]


def payer_name(t):
    cp = t.counterparty_name
    if isinstance(cp, str) and cp.strip():
        return cp.strip().upper()
    s = t.text if isinstance(t.text, str) else ""
    s = s.upper()
    for c in _C:
        m = c.search(s)
        if m:
            v = m.group(1).strip(" |")
            if len(v) >= 3:
                return v
    return ""
