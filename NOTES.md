# Experiment log (score = official scorer)

| when | change | dev 2024H2 | holdout 2025H1 | LIVE |
|---|---|---|---|---|
| 10:30 | starter (naive exact amount + doc number) | – | 11.22 (2021-24: 11.75) | – |
| 10:55 | pipeline skeleton, fallback only | 2.28 | – | – |

## Findings
- Segment mix (TRAIN): ar_ref_noise 21 %, ap_payment_multi 11.7 %, ar_1to1 9.5 %, ap_payment_single 9.4 %,
  ar_same_amount_fifo 3.3 %, bank fees 3.2+1.6 %, ar_1toN_text 3.1 %, ar_short_discount 2.9 % …
- AP payments carry `end_to_end_id` = payment-run id (camt) -> exact lookup in payment_runs/.
- train-lite contains ~214k 2021 items never settled and absent from LIVE's open items: dropped in simulation.
