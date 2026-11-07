# Roadmap: where the remaining points are

Status at v2: **LIVE 77.00** = 50 % × allocation F1 84.7 % (precision 97.8 / recall 74.6) + 30 % × transaction
accuracy 83.6 % + 20 % × exception macro-F1 48.0 %.

| Part | Weight | v2 | Points earned | Points still available |
|---|---|---|---|---|
| Allocation F1 | 50 % | 84.7 % | 42.4 | 7.6 |
| Transaction accuracy | 30 % | 83.6 % | 25.1 | 4.9 |
| Exception macro-F1 | 20 % | 48.0 % | 9.6 | **10.4** |

Two facts drive the priorities:

- **Exception macro-F1 is the average of the F1 of each of the 10 exception classes.** A rare class (e.g.
  `DUPLICATE_PAYMENT`, ~660 LIVE lines) weighs as much as a frequent one (`FX_DIFFERENCE`, `PARTIAL`). Taking one class
  from 0 to 0.8 F1 is worth **~1.6 points**.
- **We are precise (97.8 %) but miss a quarter of the allocations (recall 74.6 %).** The work is finding more matches,
  not removing wrong ones. Fixing 1 % of the bank lines (~2,000) is worth ~0.3 points of accuracy, plus some allocation F1.

---

## Block A: exception classes we get wrong (cheap, high value, ~+3–4 points)

| Class | What it means (POLICIES §3) | Problem in v2 | Fix |
|---|---|---|---|
| `DUPLICATE_PAYMENT` | An invoice that was already settled is paid again. The later line gets the flag, and the amount is what was paid again. | **0 %.** Once an item is paid we forget it, so the second payment finds nothing and becomes `UNIDENTIFIED`. | Keep an index of recently settled items. A payment that matches a settled item (reference / payer + amount) gets `DUPLICATE_PAYMENT`. Our own duplicate payments to suppliers work the same way. When the supplier refunds such a duplicate, the line is category `SUPPLIER_REFUND`. |
| `RETURNED` | A collection or payment comes back and re-opens the item: direct-debit return, returned cheque, card chargeback, our transfer rejected by the supplier's bank. | Direct-debit returns 51 %; cheques, chargebacks and rejected transfers **0 %**: only direct debits are handled. | Add the other return types (match them against the deposit, settlement or payment run that originally paid them). Investigate the direct-debit returns we miss. |
| `OVERPAYMENT` | The customer paid more than the open amount of **one** invoice; the invoice is fully cleared. | We flag ~1,070 LIVE lines, but only ~340 are real. Most of ours are multi-invoice payments where only one reference was found. | Only flag when no other open items of the payer explain the excess. |
| `UNIDENTIFIED` | A receipt with **no** open item (unknown payer, wrong beneficiary…). | Every unmatched receipt gets it: ~26.7k lines, but only ~5k are truly unidentified, so the precision of this class is about 20 %. | Flag `UNIDENTIFIED` only when the payer is unknown. When the payer is known, give our best guess. |
| `SHORT_BANK_CHARGES` | A non-SEPA transfer where an intermediary bank took a fee (≤ 60 EUR and ≤ 2 %). | 23 % | Better detection of non-SEPA payments (bank code, SWIFT text, foreign IBAN). |
| `PARTIAL` | The item still has a balance after the payment. | Over-used: many of ours are really disputes, retentions, instalments… | Mostly fixed by block C. |

## Block B: large-volume matching segments (~+2–3 points)

| Segment | LIVE lines | v2 accuracy | What's going on | Fix |
|---|---|---|---|---|
| `ar_ref_noise` | ~42k | 77.5 % | No usable invoice number. Only the payer name (truncated, trade name, renamed company) and the amount. ~9.4k lines wrong: the biggest pool. | Better name matching: prefix match on truncated words, historical names, parent/group payers, IBAN history. |
| `ar_same_amount_month` | ~4.4k | 48 % | Fixed monthly fees (same amount every month). The text names the month ("MANT. MAYO 25"). | Monthly fees are invoiced at the start of the following month, so "MAYO" means the invoice issued in June. |
| `ar_1toN_credit_note` | ~3.3k | 34 % | Several invoices minus credit notes, no reference. | Smarter subset selection that adds credit notes whose related invoice is in the set. |
| `ap_supplier_refund` | ~2.6k | 38 % | A supplier refunds one of its credit notes (it settles the `APC-` item). | Match incoming money against suppliers' open credit notes (name + amount). |
| `ar_dd_represented_transfer` | ~1k | 50 % | A returned direct debit later paid by transfer. | Comes with the returns work in block A. |
| `ar_n_to_m` | ~1.8k | 18 % | One payment for invoices of several related customers. | Hard; later. |

## Block C: document PDFs, not used yet (~+2–3 points)

There are 58k PDFs (`pdfs/<entity>/*.pdf` + `index.csv`). POLICIES §5.6–5.9 say some facts exist **only** there:

- instalments ("3 vencimientos"): every instalment except the last is `PARTIAL`;
- guarantee retention 5–10 %: the first payment is `PARTIAL`; the release ~12 months later has no exception;
- "a deducir anticipo": a proforma was paid before (that earlier receipt is `CUSTOMER_ADVANCE`); the invoice payment is
  `PARTIAL`;
- "anula y sustituye a la factura X": a payment citing X settles the replacement invoice;
- the service month of maintenance invoices;
- credit note refunded vs offset, rappel credit note paid out vs offset;
- new IBAN notices.

Segments that depend on it: instalments (~4.7k, 64 %), retentions (~1k, 25 %), advance deductions (260, 27 %), replaced
invoices (260, 26 %), customer advances (280, 0 %), and parts of supplier refunds and partials: **~12k lines**.
Cost: extract the text of all PDFs once (~15–20 min, cached), write one regex per clause (the PDFs are templated), and
use the clauses in the matcher.

## Block D: honest evaluation

`sample/` (June 2025) overestimates LIVE by ~3.5 points: a single month with fewer edge cases, and the category model
had seen June while training. Our evaluation on TRAIN uses held-out months (default 2025-04 and 2025-05):

- open balances at the start of the window are rebuilt from the TRAIN labels (what was still unpaid);
- the category model is retrained **without** those months;
- the official scorer only scores the bank lines of the window.

```
python solution/bankrec.py --phase dev --out sub_dev.csv.gz --why why_dev.csv
python solution/tools/score_dev.py sub_dev.csv.gz
```

We only produce and upload a LIVE file when the dev score improves clearly.

## Block E: FINAL risks (one shot)

- New subsidiary ZA01 from 2026-01-01: ZAR currency, a new bank, its own "KWI…" invoice numbering, renamed on 2026-04-01.
- Updated masters in `final/masters_final/` (the code already prefers them when present).
- Customers pay more slowly in 2026, and IT01 changed its collections account on 2026-02-01 (both accounts are valid).

Before the final upload: run `--phase final` and check the run log for errors, `validate.py`, and look at accuracy by entity
for ZA01.

## Working agreement

- Each model is a frozen branch `model/vN`; work happens on `model/vN+1` (or `model/vN-<topic>` per person).
- Blocks A, C and D are independent; they can be done in parallel and merged into `main` when the dev score improves.
- Log every submission in `SUBMISSIONS.md`.
