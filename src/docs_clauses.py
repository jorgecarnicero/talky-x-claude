"""Free-text clause parsing for document PDFs (invoices, credit notes, proformas, bank-account notices).

parse_clauses(text, doc_type) -> dict with the CLAUSE_COLS below (None / "" when absent). Multilingual patterns
(EN/ES/DE/FR/IT/PT); amounts accept 1,234.56 / 1.234,56 / 1 234,56.
"""
import re

CLAUSE_COLS = [
    "doc_total", "doc_currency",
    "n_instalments", "instalment_days", "instalment_amounts",
    "retention_pct", "retention_amount", "retention_release_date", "amount_payable",
    "advance_proforma", "advance_amount", "net_due",
    "replaces_document", "replaces_date", "cancels_document",
    "service_month",
    "rappel_period", "rappel_mode", "cn_mode",
    "factor_name",
    "new_iban", "old_iban", "iban_effective_date",
    "clause_text",
]

NUM = r"\d{1,3}(?:[.,\s ]\d{3})*(?:[.,]\d{1,2})|\d+(?:[.,]\d{1,2})?"
IBAN = r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}"
DATE = r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}"
DOCNO = r"[A-Z]{0,5}[-/]?\d[\w\-/]*\d"

MONTHS = {}
for i, names in enumerate([
        "january enero januar janvier gennaio janeiro jan ene janv genn",
        "february febrero februar fevrier février febbraio fevereiro feb fev févr",
        "march marzo märz marz mars marco março mar mär",
        "april abril avril aprile apr abr avr",
        "may mayo mai maggio maio",
        "june junio juni juin giugno junho jun",
        "july julio juli juillet luglio julho jul",
        "august agosto august aout août ago aug",
        "september septiembre setiembre septembre settembre setembro sep sept set",
        "october octubre oktober octobre ottobre outubro oct okt ott out",
        "november noviembre novembre novembro nov",
        "december diciembre dezember decembre décembre dicembre dezembro dec dic dez déc"], 1):
    for n in names.split():
        MONTHS.setdefault(n, i)
FOOTER = "Documento sintético de prueba — sin validez fiscal / Synthetic test document"


def num(s):
    if s is None:
        return None
    s = s.strip().replace(" ", " ")
    m = re.search(r"[.,](\d{1,2})$", s)
    if m:
        ip, dec = s[:m.start()], m.group(1)
    else:
        ip, dec = s, "0"
    ip = re.sub(r"[.,\s]", "", ip)
    if not ip.isdigit():
        return None
    return float(ip + "." + dec)


def iso(d):
    if not d:
        return ""
    m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", d)
    if not m:
        return ""
    dd, mm, yy = int(m.group(1)), int(m.group(2)), m.group(3)
    if len(yy) == 2:
        yy = "20" + yy
    if mm > 12 and dd <= 12:
        dd, mm = mm, dd
    return f"{yy}-{mm:02d}-{dd:02d}"


def _month(text):
    t = text.lower()
    pats = [r"(?:month of|mes de|correspondiente a|monat|mois de|mese di|m[eê]s de|for)\s+([a-zéèêûäöüç]+)\.?\s+(?:de\s+|del\s+)?(\d{4})",
            r"\b(?:mant\.?|mantenimiento|maintenance|wartung|entretien|manutenzione|manutenção)\b[^.]{0,60}?\b([a-zéèêûäöüç]+)\s+(?:de\s+)?(\d{4})"]
    for p in pats:
        for m in re.finditer(p, t):
            mo = MONTHS.get(m.group(1).strip("."))
            if mo:
                return f"{m.group(2)}-{mo:02d}"
    return ""


def parse_clauses(text, doc_type=""):
    out = {c: None for c in CLAUSE_COLS}
    if not text:
        return out
    j = re.sub(r"\s+", " ", text.replace(FOOTER, " ")).strip()

    # TOTAL of the document (last 'TOTAL ... <amount> <CUR>')
    tots = re.findall(r"\b(?:TOTAL(?: TTC)?|GESAMT|TOTALE)\s+(" + NUM + r")\s*([A-Z]{3})\b", j)
    if tots:
        out["doc_total"], out["doc_currency"] = num(tots[-1][0]), tots[-1][1]

    # clause text: what follows the payment-terms label (+ the IBAN line), i.e. the free-text notes
    m = re.search(r"(?:Terms|Forma de pago|Zahlungsbedingungen|Conditions|Condiciones|Condições|Pagamento|"
                  r"Condizioni|Modalités|Payment terms)\s*:", j)
    out["clause_text"] = j[m.start():][:600] if m else j[-400:]

    # instalments: '3 instalments at 60/90/120 days (4,173.17 / 4,173.17 / 4,173.19 EUR)'
    m = re.search(r"(\d+)\s*(?:instalments?|installments?|vencimientos|plazos|Raten|échéances|echeances|rate|"
                  r"presta[çc][õo]es|parcelas)\s*(?:at|a|zu|à|a)?\s*([\d/ ]+?)\s*(?:days|días|dias|Tage|jours|giorni)"
                  r"\s*\(([^)]*)\)", j, re.I)
    if m:
        out["n_instalments"] = int(m.group(1))
        out["instalment_days"] = re.sub(r"\s", "", m.group(2))
        parts = [p.strip() for p in re.split(r"\s*/\s*", re.sub(r"\s*[A-Z]{3}\s*$", "", m.group(3)))]
        amts = [num(p) for p in parts if num(p) is not None]
        out["instalment_amounts"] = ";".join(f"{a:.2f}" for a in amts)

    # retention: 'Retention 5% (3,343.99 USD) withheld until final acceptance (26/09/2025). Amount payable at due
    # date: 63,535.87 USD.' / 'Retención en garantía del 5 % (1.608,05 EUR), liberable a la recepción definitiva (..)'
    m = re.search(r"(?:Retention|Retenci[óo]n en garant[íi]a(?: del)?|Garantieeinbehalt|Einbehalt|Retenue de garantie"
                  r"(?: de)?|Ritenuta(?: a garanzia)?(?: del)?|Reten[çc][ãa]o(?: de garantia)?(?: de)?)\s*(\d+(?:[.,]\d+)?)"
                  r"\s*%\s*\((" + NUM + r")\s*[A-Z]{3}\)[^(]*\((" + DATE + r")\)", j, re.I)
    if m:
        out["retention_pct"] = num(m.group(1))
        out["retention_amount"] = num(m.group(2))
        out["retention_release_date"] = iso(m.group(3))
        m2 = re.search(r"(?:Amount payable at due date|Importe a pagar al vencimiento|Zahlbar bei Fälligkeit|"
                       r"Montant payable à l'échéance|Importo da pagare alla scadenza|Valor a pagar no vencimento)"
                       r"\s*:?\s*(" + NUM + r")", j, re.I)
        if m2:
            out["amount_payable"] = num(m2.group(1))

    # advance against proforma
    m = re.search(r"(?:advance received against proforma|anticipo recibido seg[úu]n proforma|Anzahlung (?:gem[äa]ß|laut) "
                  r"Proforma|acompte re[çc]u selon proforma|anticipo ricevuto (?:secondo|come da) proforma|"
                  r"adiantamento recebido (?:segundo|conforme) proforma)\s*(" + DOCNO + r")\s*(?::|de|von|du|di)?\s*("
                  + NUM + r")", j, re.I)
    if m:
        out["advance_proforma"] = m.group(1)
        out["advance_amount"] = num(m.group(2))
        m2 = re.search(r"(?:Net amount due|Importe neto a pagar|Nettobetrag fällig|Net à payer|Importo netto da pagare|"
                       r"Valor l[íi]quido a pagar)\s*:?\s*(" + NUM + r")", j, re.I)
        if m2:
            out["net_due"] = num(m2.group(1))

    # cancels and replaces
    m = re.search(r"(?:cancels and replaces invoice|anula y sustituye a la factura|storniert und ersetzt (?:die )?Rechnung|"
                  r"annule et remplace la facture|annulla e sostituisce la fattura|anula e substitui a fatura)\s*(\S+?)"
                  r"[,.]?\s+(?:dated|de|vom|du|del|di)\s+(" + DATE + r")", j, re.I)
    if m:
        out["replaces_document"] = m.group(1).rstrip(",.")
        out["replaces_date"] = iso(m.group(2))
    m = re.search(r"(?:CANCELLING OF INVOICE|anulaci[óo]n total de la factura|Storno (?:der )?Rechnung|"
                  r"annulation (?:totale )?de la facture|annullamento (?:totale )?della fattura|"
                  r"anula[çc][ãa]o (?:total )?da fatura)\s*(?:N[º°o]\.?)?\s*(\S+)", j, re.I)
    if m:
        out["cancels_document"] = m.group(1).rstrip(",.")

    # service month (monthly fees)
    sm = _month(j)
    out["service_month"] = sm or None

    # rappel / volume rebate
    m = re.search(r"(?:Volume rebate|Rappel)\s*([QT]\d\s*\d{4}|\d{4}\s*[QT]\d|[QT]\d)", j, re.I)
    if m or re.search(r"rappel|volume rebate|bonus|ristorno", j, re.I):
        out["rappel_period"] = re.sub(r"\s", "", m.group(1)) if m else ""
    OFFSET = (r"offset against your next payment|deducted from your next payment|se compensar[áa]|compensado|"
              r"compensará|verrechnet|compens[ée]|compensato|a deducir de su pr[óo]ximo pago")
    REFUND = (r"paid to your bank account|refunded by bank transfer|se abonar[áa] por transferencia|reembolsad|"
              r"überwiesen|erstattet|rembours|rimborsat|pago por transfer[êe]ncia|transferencia a su cuenta")
    off, ref = re.search(OFFSET, j, re.I), re.search(REFUND, j, re.I)
    mode = "offset" if off and not ref else ("paid_out" if ref and not off else None)
    if out["rappel_period"] is not None:
        out["rappel_mode"] = mode
    if doc_type in ("credit_note", "supplier_credit_note"):
        out["cn_mode"] = {"offset": "offset", "paid_out": "refund"}.get(mode)

    # factoring / confirming assignment
    m = re.search(r"(?:has been assigned to|ha sido cedido a|wurde abgetreten an|a été cédée à|è stato ceduto a|"
                  r"foi cedido a)\s+(.+?)[.]\s", j + " ", re.I)
    if m:
        out["factor_name"] = m.group(1).strip()

    # new bank account (notices and supplier invoices)
    m = re.search(r"(?:from|a partir del?|ab dem|ab|à partir du|a partire dal|a partir de)\s+(" + DATE + r").{0,160}?"
                  r"(?:account|cuenta|Konto|compte|conto|conta|IBAN)\s*:?\s*(" + IBAN + r")", j, re.I)
    if m:
        out["iban_effective_date"] = iso(m.group(1))
        out["new_iban"] = m.group(2)
        m2 = re.search(r"(?:previously|anterior|bisher|pr[ée]c[ée]dent|precedente|anteriormente)\s*:?\s*(" + IBAN + r")",
                       j, re.I)
        if m2:
            out["old_iban"] = m2.group(1)
    elif doc_type == "bank_account_notice":
        ibans = re.findall(IBAN, j)
        if ibans:
            out["new_iban"] = ibans[0]
            out["old_iban"] = ibans[1] if len(ibans) > 1 else None
        dts = re.findall(DATE, j)
        if len(dts) > 1:
            out["iban_effective_date"] = iso(dts[1])
    return out
