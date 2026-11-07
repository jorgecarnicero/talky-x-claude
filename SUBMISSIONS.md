# Models and submissions

Every model we submit is frozen as a branch named `model/vN`. Nobody commits to a `model/vN` branch after it has been
submitted. New work happens on the next `model/vN+1` branch and is merged into `main` when it beats the current best.
Produce the file from that branch (`python solution/bankrec.py --phase live --out sub_live_vN.csv.gz`) and validate it
(`python solution/tools/validate.py sub_live_vN.csv.gz --phase live`).

**Compare models on dev** (held-out TRAIN months 2025-04 and 2025-05, see `docs/ROADMAP.md` block D). Dev is within
~0.7 points of LIVE (v2: dev 77.73, LIVE 77.00), while sample overestimates by ~3.5. Only upload when dev clearly improves.

| Model | Branch | Sample | Dev | LIVE | LIVE alloc F1 (P / R) | LIVE txn acc | LIVE exc F1 | Notes |
|---|---|---|---|---|---|---|---|---|
| starter | – | 11.28 | – | – | – | – | – | official naive baseline |
| v1 | *(not saved: superseded in place)* | 79.27 | – | **75.81** | 83.0 % (98.0 / 71.9) | 82.9 % | 47.2 % | first cascade: batches, returns, remittances, refs, payer+amount, category model |
| v2 | `model/v2` | 80.66 | 77.73 | **77.00** | 84.7 % (97.8 / 74.6) | 83.6 % | 48.0 % | + oldest-due subset search, FX in item currency, confirming = latest due on a business day, txt-advice fix |
| v3 | `model/v3` | 85.28 | 82.90 | **82.25** | 85.4 % (98.3 / 75.4) | 87.4 % | 66.8 % | block A + quick block B (see below) |
| v4 | `model/v4-pdfs` | – | – | – | | | | work in progress: document PDFs (block C) |

### v3 changes (dev 77.73 → 82.90; alloc F1 85.8 → 86.6, txn acc 84.0 → 87.3, exception F1 48.2 → 67.1)

- Duplicate payments: recently settled items are kept (open = 0); a payment of a settled item by reference or payer +
  amount → `DUPLICATE_PAYMENT` (dev segment 0 → 80 %).
- Returns: direct-debit returns use the batch id in the text and the receipt actually collected (59 → 96 %, and
  re-presented transfers 59 → 89 %); returned cheques (65 → 92 %); card chargebacks; supplier transfers rejected by the
  bank (0 → 89 %).
- Wording → category: proforma → `CUSTOMER_ADVANCE` (0 → 100 %), refund of a duplicate → `CUSTOMER_REFUND` (46 → 97 %),
  supplier refund of our duplicate → `SUPPLIER_REFUND`.
- Supplier refunds of credit notes: incoming money from a supplier, or quoting the original invoice → the `APC` item
  (35 → 98 %).
- Service month: "MANT. MAYO 25" / "SERVICE APRIL 2025" picks the invoice issued the following month (56 → 88 %).
- Guarantee retention (5 / 10 %) → `PARTIAL` with the cash; `SHORT_WITHHOLDING` only for MX01 / BR01 / MA01 (26 → 88 %).
- Bank charges: also without a reference, non-SEPA detection via `XBCT`, "GASTOS SHA", foreign IBAN (18 → 78 %).
- Overpayment only for a single invoice with ≤ 10.5 % excess.
- A/B test: flagging every unmatched receipt `UNIDENTIFIED` beats "only when the payer is unknown" (81.65 vs 81.35).

## LIVE v1: weakest segments (accuracy per bank line)

| Segment | ~lines | Accuracy |
|---|---|---|
| ar_duplicate_payment | 590 | 0 % |
| customer_advance | 280 | 0 % |
| ap_returned_transfer / ar_cheque_returned / ar_card_chargeback | 270 | 0 % |
| ap_duplicate_payment / ap_duplicate_refund | 140 | 0–1.5 % |
| ar_n_to_m | 1,800 | 18 % |
| ar_short_bank_charges | 150 | 23 % |
| ar_replaced_invoice | 260 | 25 % |
| ar_retention | 1,000 | 25.5 % |
| ar_advance_deduction | 260 | 27 % |
| ar_1toN_credit_note | 3,300 | 32.5 % |
| ap_supplier_refund | 2,600 | 38 % |
| ar_overpayment / customer_refund | 730 | 41–42 % |
| ar_fx | 760 | 45 % |
| ar_same_amount_month | 4,400 | 49 % |
| ar_dd_represented_transfer / ar_dd_returned | 4,500 | 50–51 % |
| ap_confirming | 850 | 53 % |
