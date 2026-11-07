# Headroom of run `v35` — bloque `excepciones`

- Score 92.87, faltan 7.13 puntos. Este bloque: **3.00** puntos de segmentos (42 % del gap, suma de márgenes aislados), 587 movimientos mal.
- Segmentos del bloque: 19; clases: OVERPAYMENT, PARTIAL, SHORT_DISPUTE, FX_DIFFERENCE, SHORT_BANK_CHARGES, DUPLICATE_PAYMENT, SHORT_DISCOUNT, SHORT_WITHHOLDING, RETURNED. Mapa en `solution/tools/blocks.yaml` (solo para informar; el pipeline no lo lee).

## Segmentos

| segmento | movimientos | accuracy | acc LIVE | mal | puntos | recuperables | (a) pares | (b) exc. | (c) imp. | (d) cat. | (e) filas |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ar_overpayment | 135 | 0.370 | – | 85 | 0.97 | 0.55 | 85 | 0 | 0 | 0 | 0 |
| ar_short_dispute | 348 | 0.796 | – | 71 | 0.80 | 0.63 | 71 | 0 | 0 | 0 | 0 |
| ar_fx | 263 | 0.734 | – | 70 | 0.28 | 0.12 | 69 | 0 | 0 | 0 | 1 |
| ar_discount_late | 269 | 0.680 | – | 86 | 0.18 | 0.10 | 84 | 2 | 0 | 0 | 0 |
| ar_short_withholding | 491 | 0.919 | – | 40 | 0.17 | 0.09 | 32 | 2 | 0 | 0 | 6 |
| ar_duplicate_payment | 228 | 0.855 | – | 33 | 0.17 | 0.00 | 32 | 1 | 0 | 0 | 0 |
| ar_short_discount | 2355 | 0.949 | – | 119 | 0.15 | – | 104 | 12 | 0 | 0 | 3 |
| ap_fx | 411 | 0.993 | – | 3 | 0.13 | – | 2 | 0 | 0 | 0 | 1 |
| ar_dd_returned | 1214 | 0.978 | – | 27 | 0.06 | – | 27 | 0 | 0 | 0 | 0 |
| ar_short_bank_charges | 51 | 0.961 | – | 2 | 0.04 | – | 2 | 0 | 0 | 0 | 0 |
| ar_partial | 1333 | 0.973 | – | 36 | 0.03 | – | 16 | 18 | 0 | 0 | 2 |
| ar_dispute_settlement | 373 | 0.965 | – | 13 | 0.01 | – | 2 | 11 | 0 | 0 | 0 |
| ap_duplicate_refund | 26 | 0.923 | – | 2 | 0.01 | – | 0 | 0 | 0 | 2 | 0 |

## Clases de excepción

| clase | F1 | soporte | puntos (filas) | puntos (líneas) | puntos a F1 0,9 |
|---|---:|---:|---:|---:|---:|
| OVERPAYMENT | 0.505 | 135 | 1.07 | 1.08 | 0.79 |
| PARTIAL | 0.863 | 4376 | 0.92 | 1.41 | 0.07 |
| SHORT_DISPUTE | 0.920 | 976 | 0.77 | 0.81 | 0.00 |
| FX_DIFFERENCE | 0.847 | 5726 | 0.65 | 0.80 | 0.11 |
| SHORT_BANK_CHARGES | 0.800 | 52 | 0.41 | 0.43 | 0.20 |
| DUPLICATE_PAYMENT | 0.876 | 249 | 0.28 | 0.30 | 0.05 |
| SHORT_DISCOUNT | 0.969 | 5997 | 0.21 | 0.28 | 0.00 |
| SHORT_WITHHOLDING | 0.938 | 1340 | 0.17 | 0.18 | 0.00 |
| RETURNED | 0.981 | 1478 | 0.06 | 0.06 | 0.00 |

