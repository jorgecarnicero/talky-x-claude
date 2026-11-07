# Headroom of run `v35` — bloque `matching`

- Score 92.87, faltan 7.13 puntos. Este bloque: **3.61** puntos de segmentos (51 % del gap, suma de márgenes aislados), 1761 movimientos mal.
- Segmentos del bloque: 36; clases: UNIDENTIFIED. Mapa en `solution/tools/blocks.yaml` (solo para informar; el pipeline no lo lee).

## Segmentos

| segmento | movimientos | accuracy | mal | puntos | recuperables | (a) pares | (b) exc. | (c) imp. | (d) cat. | (e) filas |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ar_ref_noise | 14940 | 0.954 | 691 | 1.50 | 0.29 | 637 | 3 | 0 | 0 | 51 |
| ar_1toN_credit_note | 1079 | 0.666 | 360 | 0.88 | 0.60 | 209 | 0 | 0 | 0 | 151 |
| ar_n_to_m | 600 | 0.387 | 368 | 0.54 | 0.26 | 357 | 2 | 0 | 0 | 9 |
| ar_1toN_text | 2048 | 0.934 | 135 | 0.34 | 0.32 | 119 | 0 | 0 | 0 | 16 |
| ap_confirming | 261 | 0.843 | 41 | 0.11 | 0.00 | 41 | 0 | 0 | 0 | 0 |
| ar_same_amount_fifo | 2024 | 0.959 | 82 | 0.06 | – | 82 | 0 | 0 | 0 | 0 |
| ar_dd_represented_transfer | 311 | 0.942 | 18 | 0.04 | – | 9 | 9 | 0 | 0 | 0 |
| unidentified | 1160 | 0.992 | 9 | 0.04 | – | 0 | 0 | 0 | 9 | 0 |
| unidentified_lookalike | 478 | 0.979 | 10 | 0.03 | – | 0 | 0 | 0 | 10 | 0 |
| ar_third_party_confirming | 293 | 0.942 | 17 | 0.02 | – | 17 | 0 | 0 | 0 | 0 |
| ar_dd_batch | 540 | 0.991 | 5 | 0.02 | – | 0 | 5 | 0 | 0 | 0 |
| ar_1to1 | 6725 | 0.999 | 10 | 0.01 | – | 8 | 1 | 0 | 0 | 1 |
| ar_third_party_agency | 123 | 0.919 | 10 | 0.01 | – | 8 | 0 | 0 | 0 | 2 |
| ar_third_party_parent | 51 | 0.941 | 3 | 0.01 | – | 2 | 0 | 0 | 0 | 1 |
| ar_cheque_deposit | 675 | 0.999 | 1 | 0.00 | – | 1 | 0 | 0 | 0 | 0 |
| ap_payment_multi | 8323 | 1.000 | 1 | 0.00 | – | 0 | 0 | 1 | 0 | 0 |

## Clases de excepción

| clase | F1 | soporte | puntos (filas) | puntos (líneas) | puntos a F1 0,9 |
|---|---:|---:|---:|---:|---:|
| UNIDENTIFIED | 0.705 | 1638 | 0.64 | 4.46 | 0.39 |

