"""Keyword / structure rules for bank lines that settle no item (POLICIES §4).

classify(text, amount, bank_code) -> category or None. Pure function on one line; pairing logic (bank reversals,
intl-fee lines) lives in passes/nonitem.py.
"""
import re

# (category, sign, regex) ; sign: '+' receipts only, '-' payments only, '' both. Order matters (first hit wins).
RULES = [
    # bank reversal markers handled by pairing, but the "ANULACION" line itself is always BANK_REVERSAL
    ("BANK_REVERSAL", "", r"ANULACION OPERACION INDEBIDA|OPERACION INDEBIDA|ANULACION APUNTE ERRONEO"),
    ("CASH_POOL", "", r"CASH CONCENTRATION|ZBA SWEEP|CASH POOLING|NIVELACION SALDO|CASH ?POOL|ZERO BALANC"
                      r"|TRASPASO ENTRE CUENTAS|TRASPASO PROPIO"),
    ("INTERCOMPANY", "", r"\bIC VALTERMIA|FINANCIACION IC\b|TRANSF\.? IC\b|PRESTAMO INTRAGRUP|PRESTAMO IC\b|INTRAGRUPO"
                         r"|INTERCOMPANY|DEVOLUCION PRESTAM|\bVALTERMIA\b"),
    ("CUSTOMER_ADVANCE", "+", r"PROFORMA|\bPF\d\d-\d"),
    ("CUSTOMER_REFUND", "-", r"\bREFUND\b|PAGO DUPLICADO|DUPLICAD|PAGO DOBLE|DEVOLUCION PAGO DU"),
    ("SUPPLIER_REFUND", "+", r"PAGO DOBLE|DUPLICAD|DOUBLE PAYMENT|DUPLICATE PAYMENT|DEVOLUCION PAGO DU"),
    ("INTERNAL_TRANSFER", "", r"TRASPASO (DE|A) CTA"),
    ("FX_DEAL", "", r"\bFX DEAL\b|COMPRA DIVISA|VENTA DIVISA|COMPRAVENTA DIVISA|FX SPOT|FX FORWARD"),
    ("PAYROLL", "-", r"\bNOMINAS?\b|\bPAYROLL\b|\bSALARIOS\b|^(SALARIES|SALARY|WAGES)\b"),
    ("SOCIAL_SECURITY", "-", r"SOCIAL CHARGES|SEGURIDAD SOCIAL|\bTGSS\b|SEG\.? SOCIAL|URSSAF|COTIZACION"),
    ("TAX_REFUND", "+", r"DEVOLUCION AEAT|TAX REFUND|DEVOLUCION IMPUESTO|VAT REFUND"),
    ("TAX_PAYMENT", "-", r"IMPUESTOS MOD|TAX PAYMENT|VAT RETURN|TAX AUTHORITY|DGFIP|\bAEAT\b|HACIENDA"),
    ("LOAN_DRAWDOWN", "+", r"DISPOSICION POLIZA|DISPOSICION PRESTAMO|LOAN DRAWDOWN"),
    ("LOAN_REPAYMENT", "-", r"AMORTIZACION (DE )?PRESTAMO|AMORTIZACION POLIZA|^PRES\.[A-Z]{2}\d|LOAN REPAYMENT"),
    ("LEASING", "-", r"CUOTA (LEASING|LSG|RENTING|ARRENDAMIENTO)|\bLEASING\b"),
    ("INSURANCE", "-", r"INSURANCE PREMIUM|SEGUROS? MULTIRRIESGO|PRIMA SEGURO|POLIZA SEGURO"),
    ("CARD_EXPENSES", "-", r"ADEUDO MENSUAL DE TARJETA|CORPORATE CARD|TARJETA EMPRESA"),
    ("INTEREST", "", r"LIQUIDACION INTERESES|INTEREST SETTLEMENT|INTERESES (A FAVOR|DEUDORES|ACREEDORES)"),
    ("BANK_FEES", "-", r"^(FRAIS VIREMENT|COMISION TRANSFERENCIA|COM\. TRANSF|COMISION TRANSF|SERV\. EM\. TRANSF)"
                       r"|COMISION DEVOLUCION|COMISION REMESA|COMISION MANTENIMIENTO|COMISIONES SERVICIOS"
                       r"|LIQUIDACION DE INTERESES, COMISIONES|ACCOUNT MAINTENANCE FEE|LIQUIDACION CTA\.? ?CTO"
                       r"|^COMISION|^FRAIS\b"
                       r"|^(SERVICE FEE|MONTHLY (ACCOUNT |SERVICE )?FEE|BANK CHARGES?\b|ACCOUNT FEE|TRANSACTION FEE|COMMISSION)"),
]
# Fallback on standard bank transaction codes (camt BkTxCd / MT940 type), used only when no text rule fires.
# Checked on TRAIN: every one of these codes is 100 % the given category.  sign as above.
CODE_RULES = [
    ("BANK_FEES", "-", r"(^|/)CHRG$|^NCHG$|^17$"),
    ("CASH_POOL", "", r"/ZABA$"),
    ("PAYROLL", "-", r"/SALA$|^NSAL$"),
    ("TAX_PAYMENT", "-", r"/TAXS$"),
    ("FX_DEAL", "", r"^FORX/|^NFEX$"),
    ("LOAN_REPAYMENT", "-", r"^LDAS/.*/PPAY$|^NLDP$|^05$"),
    ("CARD_EXPENSES", "-", r"/CCRD/"),
    ("INTEREST", "", r"/INTR$|^NINT$"),
]
_CRX = [(c, s, re.compile(r)) for c, s, r in CODE_RULES]
_RX = [(c, s, re.compile(r)) for c, s, r in RULES]


def _apply(rules, t, amount):
    for cat, sign, rx in rules:
        if sign == "+" and amount <= 0:
            continue
        if sign == "-" and amount >= 0:
            continue
        if rx.search(t):
            if cat == "INTEREST":
                return "INTEREST_INCOME" if amount > 0 else "INTEREST_EXPENSE"
            return cat
    return None


def classify(text, amount, bank_code=""):
    """text: upper-cased full text of the line. Returns (category, how) or (None, None)."""
    t = (text or "").upper()
    c = _apply(_RX, t, amount)
    if c:
        return c, "text"
    c = _apply(_CRX, (bank_code or "").upper().strip(), amount)
    if c:
        return c, "code"
    return None, None
