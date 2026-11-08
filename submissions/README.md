# FINAL submission

`sub_final_v8d_pf130.csv.gz` is the single file the team submitted for the blind FINAL phase.

| | |
|---|---|
| Code | tag `v8d-final` (= this repository's `scripts/run.sh final`) |
| FINAL score | **95.06** |
| Method score on LIVE | 94.67 |
| Rows | 220,256 (validated) |
| Receipts left `UNIDENTIFIED` | 4.9 % |

Difference from the previous candidate (v8c): the card-order cache is keyed by (order number, entity) instead of the order
number alone, because card order numbers repeat across entities in FINAL. This halved the `DUPLICATE_PAYMENT` rows
(1,526 → 763).
