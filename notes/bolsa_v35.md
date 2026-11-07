# Pocket of run `v35`: lines without any pass and v3rest lines

- Without any pass (answered UNIDENTIFIED / no matcher): **2957** lines. v3rest: **3002** lines. Overlap 2957; only without-pass 0, only v3rest 45.
- Correct: without-pass 1619, v3rest 1619. Union analysed below: 3002 lines, 1383 wrong.

Method: diagnostic only. The golden (and its segment column) is read to explain failures; the 'candidates' use the party of the golden items as the payer and replay the golden of earlier lines to know each item's open balance at that moment, so they describe what a perfect payer identification would face.

## Summary

1. The two sets are the same pocket: all 2,957 UNIDENTIFIED lines are v3rest lines (v3rest adds 45 more: 40 `ap_confirming` lines answered BANK_FEES,
   plus 5 others). Of the 3,002, **1,619 are correct** (every `unidentified` / `unidentified_lookalike` line, 100 %) and **1,383 are wrong**
   (every line of every other segment: 0 % correct). The pocket is all-or-nothing by segment.
2. **99.9 % of the wrong lines (1,381 of 1,383) have items in the golden**: the pipeline did not find them, it did not pick wrongly between
   an UNIDENTIFIED and a category. Only 2 are categories (CUSTOMER_REFUND answered BANK_FEES). 1,376 of them answered UNIDENTIFIED.
   Golden exceptions hidden in there: 752 plain settlements, 252+ PARTIAL, 84 OVERPAYMENT, 75+10 SHORT_DISCOUNT, 61 SHORT_DISPUTE, 32 DUPLICATE_PAYMENT.
3. Recall or selection? The golden items were **open at that moment in 96.4 %** of the cases (1,326 of 1,376), so the ledger is not the problem
   (50 lines are duplicates / returns / items consumed earlier). A golden document number is visible in the bank text in only 30.9 % (425).
   Many lines have several items (296 with 1, 266 with 2, long tail up to ~59 in `ar_short_dispute`, median 20 in `ar_1toN_credit_note`) and the payer has a median of ~40
   open items. Split: **634 lines are a recall miss** (reference visible or <= 5 candidates in range), **692 are ambiguity** (no reference,
   > 5 candidates), **50 not findable**. The biggest ambiguous segments: `ar_ref_noise` (373), `ar_n_to_m` (311), `ar_1toN_credit_note` (130).
4. OVERPAYMENT: 135 pairs in the golden, TP 50, **FN 85** (84 lines answered UNIDENTIFIED, 1 with other items), **FP 13** (precision 0.794, recall 0.370).
   It is almost never confused with another exception: the 13 FPs are lines where the golden has no exception (6: 5 in `ar_ref_noise`, 1 in `ar_1toN_credit_note`), a mix with PARTIAL (3, `ar_n_to_m`) or
   SHORT_DISCOUNT (4); 58 of the pipeline's 63 OVERPAYMENT come from `v3:ref_overpay`, 5 from `ar:over`.
   Overpayment's weakness is recall: the line is simply never matched (typically 1 golden item, median 3 payer candidates).

## 1. By golden segment

### Without any pass (2957)

| segment | lines | correct | wrong |
|---|---:|---:|---:|
| ar_ref_noise | 372 | 0 | 372 |
| ar_n_to_m | 311 | 0 | 311 |
| ar_1toN_credit_note | 130 | 0 | 130 |
| ar_overpayment | 84 | 0 | 84 |
| ar_short_discount | 80 | 0 | 80 |
| ar_short_dispute | 62 | 0 | 62 |
| ar_1toN_text | 61 | 0 | 61 |
| ar_discount_late | 54 | 0 | 54 |
| ar_duplicate_payment | 32 | 0 | 32 |
| ar_short_withholding | 26 | 0 | 26 |
| ar_same_amount_month | 23 | 0 | 23 |
| ar_fx | 22 | 0 | 22 |
| ar_same_amount_fifo | 15 | 0 | 15 |
| ar_third_party_confirming | 14 | 0 | 14 |
| ar_1toN_remittance | 11 | 0 | 11 |
| ar_partial | 10 | 0 | 10 |
| ar_instalment | 10 | 0 | 10 |
| ar_third_party_agency | 7 | 0 | 7 |
| ar_1to1 | 4 | 0 | 4 |
| ar_advance_deduction | 2 | 0 | 2 |
| ar_third_party_parent | 2 | 0 | 2 |
| ar_short_bank_charges | 1 | 0 | 1 |
| ar_dd_represented_transfer | 1 | 0 | 1 |
| ar_dispute_settlement | 1 | 0 | 1 |
| ar_new_iban | 1 | 0 | 1 |
| ar_replaced_invoice | 1 | 0 | 1 |
| ar_retention | 1 | 0 | 1 |
| unidentified | 1151 | 1151 | 0 |
| unidentified_lookalike | 468 | 468 | 0 |

### v3rest (3002)

| segment | lines | correct | wrong |
|---|---:|---:|---:|
| ar_ref_noise | 373 | 0 | 373 |
| ar_n_to_m | 311 | 0 | 311 |
| ar_1toN_credit_note | 130 | 0 | 130 |
| ar_overpayment | 84 | 0 | 84 |
| ar_short_discount | 80 | 0 | 80 |
| ar_short_dispute | 62 | 0 | 62 |
| ar_1toN_text | 61 | 0 | 61 |
| ar_discount_late | 54 | 0 | 54 |
| ap_confirming | 40 | 0 | 40 |
| ar_duplicate_payment | 32 | 0 | 32 |
| ar_short_withholding | 26 | 0 | 26 |
| ar_same_amount_month | 23 | 0 | 23 |
| ar_fx | 22 | 0 | 22 |
| ar_same_amount_fifo | 15 | 0 | 15 |
| ar_third_party_confirming | 14 | 0 | 14 |
| ar_1toN_remittance | 11 | 0 | 11 |
| ar_instalment | 10 | 0 | 10 |
| ar_partial | 10 | 0 | 10 |
| ar_third_party_agency | 7 | 0 | 7 |
| ar_1to1 | 4 | 0 | 4 |
| ar_advance_deduction | 2 | 0 | 2 |
| ar_third_party_parent | 2 | 0 | 2 |
| ar_dd_represented_transfer | 2 | 0 | 2 |
| customer_refund | 2 | 0 | 2 |
| ar_retention | 1 | 0 | 1 |
| ap_fx | 1 | 0 | 1 |
| ar_replaced_invoice | 1 | 0 | 1 |
| ar_short_bank_charges | 1 | 0 | 1 |
| ar_dispute_settlement | 1 | 0 | 1 |
| ar_new_iban | 1 | 0 | 1 |
| unidentified | 1151 | 1151 | 0 |
| unidentified_lookalike | 468 | 468 | 0 |

## 2. Wrong lines: does the golden have items (pipeline did not find them) or a category / UNIDENTIFIED (pipeline chose wrong)?

Union of the two sets, wrong lines = 1383: golden **has items** in 1381 (99.9 %), golden is **no-item** (category / UNIDENTIFIED) in 2 (0.1 %).

### 2a. Golden has items: golden exception set vs what the pipeline answered

| golden exception(s) | pipeline answered | lines |
|---|---:|---:|
| - | UNIDENTIFIED/UNIDENTIFIED | 752 |
| -+PARTIAL | UNIDENTIFIED/UNIDENTIFIED | 252 |
| OVERPAYMENT | UNIDENTIFIED/UNIDENTIFIED | 84 |
| -+SHORT_DISCOUNT | UNIDENTIFIED/UNIDENTIFIED | 75 |
| -+SHORT_DISPUTE | UNIDENTIFIED/UNIDENTIFIED | 61 |
| - | BANK_FEES/- | 40 |
| DUPLICATE_PAYMENT | UNIDENTIFIED/UNIDENTIFIED | 32 |
| SHORT_WITHHOLDING | UNIDENTIFIED/UNIDENTIFIED | 21 |
| FX_DIFFERENCE | UNIDENTIFIED/UNIDENTIFIED | 16 |
| PARTIAL | UNIDENTIFIED/UNIDENTIFIED | 16 |
| SHORT_DISCOUNT | UNIDENTIFIED/UNIDENTIFIED | 10 |
| -+PARTIAL+SHORT_DISCOUNT | UNIDENTIFIED/UNIDENTIFIED | 6 |
| -+FX_DIFFERENCE | UNIDENTIFIED/UNIDENTIFIED | 5 |
| -+SHORT_WITHHOLDING | UNIDENTIFIED/UNIDENTIFIED | 5 |
| -+PARTIAL+SHORT_DISCOUNT+SHORT_DISPUTE | UNIDENTIFIED/UNIDENTIFIED | 1 |
| -+FX_DIFFERENCE+PARTIAL | BANK_FEES/- | 1 |
| - | items | 1 |
| FX_DIFFERENCE+SHORT_DISPUTE | UNIDENTIFIED/UNIDENTIFIED | 1 |
| - | CUSTOMER_ADVANCE/- | 1 |
| SHORT_BANK_CHARGES | UNIDENTIFIED/UNIDENTIFIED | 1 |

Items per line in the golden (wrong lines): 1: 296, 2: 266, 3: 120, 4: 91, 5: 70, 6: 56, 7: 35, 8: 28, 9: 28, 10: 28, 11: 18, 12: 13

### 2b. Golden is a category / UNIDENTIFIED: golden category/exception vs pipeline answer

| golden category/exception | pipeline answered | lines |
|---|---:|---:|
| CUSTOMER_REFUND/- | BANK_FEES/- | 2 |

## 3. Wrong lines with items in the golden: were the items available, and how many candidates?

Where are the golden items on those lines? (per line)

| state | lines |
|---|---:|
| all open at start | 830 |
| all in ledger (open or issued in window) | 546 |
| some not open at start | 5 |

Replay of the golden before each line (open balance at that moment, payer = party of the golden items), 1376 lines:

- all golden items open at that moment (so a matcher could in principle find them): **1326** (96.4 %); some missing: 50.
- at least one golden document number visible in the bank text / refs: **425** (30.9 %); all of them visible: 182.
- counterparty name or account present on the bank line: 785 (57.0 %).

Candidates per line: open items of the payer at that moment

| cand_payer | lines | of which refs visible |
|---|---:|---:|
| 0 | 6 | 0 |
| 1 | 39 | 6 |
| 2 | 59 | 5 |
| 3-5 | 124 | 9 |
| 6-10 | 100 | 20 |
| 11-20 | 198 | 71 |
| 21-50 | 446 | 162 |
| 51-100 | 257 | 50 |
| >100 | 147 | 102 |

Candidates per line: of those, open amount <= payment (x1.01)

| cand_range | lines | of which refs visible |
|---|---:|---:|
| 0 | 19 | 0 |
| 1 | 78 | 12 |
| 2 | 65 | 5 |
| 3-5 | 103 | 12 |
| 6-10 | 110 | 30 |
| 11-20 | 217 | 75 |
| 21-50 | 406 | 144 |
| 51-100 | 233 | 45 |
| >100 | 145 | 102 |

n_golden (items to find per line) vs candidates in range, median / p90 by golden segment (top 12 by lines):

| segment | lines | n_golden | cand_payer_med | cand_payer_p90 | cand_range_med | refs_visible |
|---|---:|---:|---:|---:|---:|---:|
| ar_ref_noise | 373 | 6 | 41 | 74.8 | 39 | 31 |
| ar_n_to_m | 311 | 2 | 23 | 46 | 19 | 138 |
| ar_1toN_credit_note | 130 | 20 | 66.5 | 656.0 | 66.5 | 57 |
| ar_overpayment | 84 | 1 | 3 | 13.7 | 2 | 12 |
| ar_short_discount | 80 | 2 | 18 | 50.1 | 15 | 18 |
| ar_short_dispute | 62 | 59 | 488 | 987.9 | 488 | 49 |
| ar_1toN_text | 61 | 17 | 66 | 370 | 66 | 58 |
| ar_discount_late | 54 | 4 | 33 | 76.4 | 32 | 30 |
| ap_confirming | 40 | 2 | 7 | 34.2 | 6 | 0 |
| ar_duplicate_payment | 27 | 1 | 1 | 10.4 | 1 | 0 |
| ar_short_withholding | 26 | 4 | 21.5 | 30.5 | 21 | 11 |
| ar_same_amount_month | 23 | 1 | 4 | 5.8 | 4 | 0 |

Reading:

- *Recall of the matcher* (items available, reference visible or few candidates): 634 lines (425 with a visible reference, 238 with <= 5 candidates in range).
- *Selection / ambiguity* (items available, no visible reference, > 5 candidates in range): 692 lines.
- *Not findable by replay* (a golden item is not open at that moment, e.g. duplicates, returns, items settled by earlier lines): 50 lines.

## 4. OVERPAYMENT confusion on the dev window

Item-pair level (pairs where golden or pipeline says OVERPAYMENT; '-' = no exception, '(pair absent)' = pair not in that file):

| golden exception | pipeline exception | pairs |
|---|---:|---:|
| OVERPAYMENT | (pair absent) | 85 |
| OVERPAYMENT | OVERPAYMENT | 50 |
| (pair absent) | OVERPAYMENT | 9 |
| PARTIAL | OVERPAYMENT | 2 |
| - | OVERPAYMENT | 2 |

TP 50, FN 85, FP 13: precision 0.794, recall 0.370.

False negatives by what the pipeline did on the line (85 lines):

| pipeline answer | lines |
|---|---:|
| answered UNIDENTIFIED/UNIDENTIFIED (no items) | 84 |
| different items | 1 |

Segments of the missed OVERPAYMENT lines: ar_overpayment 85

False positives by what the golden has on the line (13 lines):

| golden | lines |
|---|---:|
| golden items, exceptions -; seg ar_ref_noise | 5 |
| golden items, exceptions -+PARTIAL; seg ar_n_to_m | 3 |
| golden items, exceptions SHORT_DISCOUNT; seg ar_replaced_invoice | 3 |
| golden items, exceptions SHORT_DISCOUNT; seg ar_short_discount | 1 |
| golden items, exceptions -; seg ar_1toN_credit_note | 1 |

Pass / matcher that emitted OVERPAYMENT: v3:ref_overpay 58, ar:over 5

