# Models and submissions

Every model we submit is frozen as a branch named `model/vN`. Nobody commits to a `model/vN` branch after it has been
submitted. New work happens on the next `model/vN+1` branch and is merged into `main` when it beats the current best.
Produce the file from that branch (`python solution/bankrec.py --phase live --out sub_live_vN.csv.gz`) and validate it
(`python solution/tools/validate.py sub_live_vN.csv.gz --phase live`).

Sample scores come from `starter/score_train.py <file> --sample`. They overestimate LIVE by about 3.5 points (sample
recall is higher), so compare models on sample, but trust LIVE.

| Model | Branch | Sample | LIVE | LIVE alloc F1 (P / R) | LIVE txn acc | LIVE exc F1 | Notes |
|---|---|---|---|---|---|---|---|
| starter | – | 11.28 | – | – | – | – | official naive baseline |
| v1 | *(not saved: superseded in place)* | 79.27 | **75.81** | 83.0 % (98.0 / 71.9) | 82.9 % | 47.2 % | first cascade: batches, returns, remittances, refs, payer+amount, category model |
| v2 | `model/v2` | 80.66 | *pending* | | | | + oldest-due subset search, FX in item currency, confirming = latest due on a business day, txt-advice fix |
| v3 | `model/v3` | – | – | | | | work in progress |

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
