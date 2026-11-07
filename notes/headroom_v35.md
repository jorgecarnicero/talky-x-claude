# Headroom of run `v35`

- Score del run: **92.87** → faltan **7.13** puntos hasta 100.
- Ventana: 2025-04-01..2025-05-31, 69553 movimientos, dataset `/Users/mateolarareguera/hackdata/data_dev`.
- Método: se reconstruye el envío del run con las filas de un segmento (o clase) sustituidas por el golden y se repuntúa con el scorer oficial (`starter/bankrec_score.py`); puntos = score nuevo − score del run. Cifras exactas, no aproximadas.
- Comprobaciones: el envío sin modificar reproduce el score de `result.json`; sustituir todos los segmentos da 100.00.

## 1. Margen por segmento

Puntos que sube el score si los movimientos del segmento pasan a ser exactamente el golden (segmentos sin fallos: 0,00, omitidos).

| segmento | movimientos | accuracy actual | mal | puntos | % del gap |
|---|---:|---:|---:|---:|---:|
| ar_ref_noise | 14940 | 0.954 | 691 | 1.50 | 21 % |
| ar_overpayment | 135 | 0.370 | 85 | 0.97 | 14 % |
| ar_1toN_credit_note | 1079 | 0.666 | 360 | 0.88 | 12 % |
| ar_short_dispute | 348 | 0.796 | 71 | 0.80 | 11 % |
| ar_n_to_m | 600 | 0.387 | 368 | 0.54 | 8 % |
| ar_1toN_text | 2048 | 0.934 | 135 | 0.34 | 5 % |
| ar_fx | 263 | 0.734 | 70 | 0.28 | 4 % |
| ar_discount_late | 269 | 0.680 | 86 | 0.18 | 3 % |
| ar_short_withholding | 491 | 0.919 | 40 | 0.17 | 2 % |
| ar_duplicate_payment | 228 | 0.855 | 33 | 0.17 | 2 % |
| ar_instalment | 1497 | 0.886 | 171 | 0.15 | 2 % |
| ar_short_discount | 2355 | 0.949 | 119 | 0.15 | 2 % |
| ap_fx | 411 | 0.993 | 3 | 0.13 | 2 % |
| ar_same_amount_month | 1422 | 0.968 | 45 | 0.12 | 2 % |
| ap_confirming | 261 | 0.843 | 41 | 0.11 | 2 % |
| ar_1toN_remittance | 381 | 0.971 | 11 | 0.08 | 1 % |
| ar_same_amount_fifo | 2024 | 0.959 | 82 | 0.06 | 1 % |
| ar_dd_returned | 1214 | 0.978 | 27 | 0.06 | 1 % |
| ar_replaced_invoice | 82 | 0.695 | 25 | 0.05 | 1 % |
| ar_dd_represented_transfer | 311 | 0.942 | 18 | 0.04 | 1 % |
| ar_retention | 304 | 0.967 | 10 | 0.04 | 1 % |
| unidentified | 1160 | 0.992 | 9 | 0.04 | 1 % |
| ar_short_bank_charges | 51 | 0.961 | 2 | 0.04 | 1 % |
| ar_partial | 1333 | 0.973 | 36 | 0.03 | 0 % |
| unidentified_lookalike | 478 | 0.979 | 10 | 0.03 | 0 % |
| ar_advance_deduction | 113 | 0.912 | 10 | 0.03 | 0 % |
| ar_third_party_confirming | 293 | 0.942 | 17 | 0.02 | 0 % |
| ar_dd_batch | 540 | 0.991 | 5 | 0.02 | 0 % |
| ar_dispute_settlement | 373 | 0.965 | 13 | 0.01 | 0 % |
| ar_1to1 | 6725 | 0.999 | 10 | 0.01 | 0 % |
| ar_third_party_agency | 123 | 0.919 | 10 | 0.01 | 0 % |
| ar_third_party_parent | 51 | 0.941 | 3 | 0.01 | 0 % |
| ap_duplicate_refund | 26 | 0.923 | 2 | 0.01 | 0 % |
| ap_factored | 477 | 0.996 | 2 | 0.01 | 0 % |
| ar_new_iban | 102 | 0.990 | 1 | 0.01 | 0 % |
| customer_refund | 126 | 0.984 | 2 | 0.00 | 0 % |
| ar_retention_release | 257 | 0.996 | 1 | 0.00 | 0 % |
| ar_cheque_deposit | 675 | 0.999 | 1 | 0.00 | 0 % |
| ap_payment_multi | 8323 | 1.000 | 1 | 0.00 | 0 % |

**Coherencia**: suma de márgenes por segmento = **7.10** frente a **7.13** que faltan (100 − 92.87); diferencia +0.03. No coinciden exactamente porque el score no es lineal (F1 de asignación y macro-F1 de excepciones son cocientes): cada segmento se mide aislado, con el resto tal como está, mientras que corregirlos todos a la vez da exactamente 100. Los 2626 movimientos mal (3.78 % de 69553) valen por sí solos 1.13 puntos del término de accuracy (peso 0,3).

## 2. Margen por clase de excepción

«Filas»: se sustituyen solo las filas de esa clase (las enviadas y las del golden) y el resto del envío queda igual. «Líneas»: se sustituyen por el golden todos los movimientos donde esa clase aparece en el envío o en el golden (incluye el efecto sobre la accuracy del movimiento). «A F1 0,9»: puntos del término macro-F1 si la clase llegara a F1 0,9 (= 100·0,2·(0,9 − F1)/10 clases con soporte; 0 si ya la supera).

| clase | F1 actual | soporte | filas enviadas | puntos (filas) | puntos (líneas) | puntos a F1 0,9 | nota |
|---|---:|---:|---:|---:|---:|---:|---|
| OVERPAYMENT | 0.505 | 135 | 63 | 1.07 | 1.08 | 0.79 |  |
| PARTIAL | 0.863 | 4376 | 4440 | 0.92 | 1.41 | 0.07 |  |
| SHORT_DISPUTE | 0.920 | 976 | 831 | 0.77 | 0.81 | 0.00 |  |
| FX_DIFFERENCE | 0.847 | 5726 | 6266 | 0.65 | 0.80 | 0.11 |  |
| UNIDENTIFIED | 0.705 | 1638 | 2957 | 0.64 | 4.46 | 0.39 |  |
| SHORT_BANK_CHARGES | 0.800 | 52 | 73 | 0.41 | 0.43 | 0.20 | (n pequeño) |
| DUPLICATE_PAYMENT | 0.876 | 249 | 244 | 0.28 | 0.30 | 0.05 |  |
| SHORT_DISCOUNT | 0.969 | 5997 | 5640 | 0.21 | 0.28 | 0.00 |  |
| SHORT_WITHHOLDING | 0.938 | 1340 | 1190 | 0.17 | 0.18 | 0.00 |  |
| RETURNED | 0.981 | 1478 | 1480 | 0.06 | 0.06 | 0.00 |  |

## 3. Taxonomía de los movimientos mal, por segmento

Cada movimiento mal cae en un único tipo, por este orden: (d) el golden no tiene partidas y la respuesta difiere; (a) se respondió sin partidas o con un conjunto de partidas que no contiene ni está contenido en el golden; (e) el conjunto enviado es subconjunto/superconjunto estricto del golden; con el mismo conjunto: (b) alguna excepción difiere, si no (c) algún importe difiere más allá de la tolerancia.

| segmento | mal | (a) pares | (b) excepción | (c) importe | (d) categoría | (e) filas +/− |
|---|---:|---:|---:|---:|---:|---:|
| ar_ref_noise | 691 | 637 | 3 | 0 | 0 | 51 |
| ar_overpayment | 85 | 85 | 0 | 0 | 0 | 0 |
| ar_1toN_credit_note | 360 | 209 | 0 | 0 | 0 | 151 |
| ar_short_dispute | 71 | 71 | 0 | 0 | 0 | 0 |
| ar_n_to_m | 368 | 357 | 2 | 0 | 0 | 9 |
| ar_1toN_text | 135 | 119 | 0 | 0 | 0 | 16 |
| ar_fx | 70 | 69 | 0 | 0 | 0 | 1 |
| ar_discount_late | 86 | 84 | 2 | 0 | 0 | 0 |
| ar_short_withholding | 40 | 32 | 2 | 0 | 0 | 6 |
| ar_duplicate_payment | 33 | 32 | 1 | 0 | 0 | 0 |
| ar_instalment | 171 | 23 | 135 | 0 | 0 | 13 |
| ar_short_discount | 119 | 104 | 12 | 0 | 0 | 3 |
| ap_fx | 3 | 2 | 0 | 0 | 0 | 1 |
| ar_same_amount_month | 45 | 43 | 1 | 0 | 0 | 1 |
| ap_confirming | 41 | 41 | 0 | 0 | 0 | 0 |
| ar_1toN_remittance | 11 | 11 | 0 | 0 | 0 | 0 |
| ar_same_amount_fifo | 82 | 82 | 0 | 0 | 0 | 0 |
| ar_dd_returned | 27 | 27 | 0 | 0 | 0 | 0 |
| ar_replaced_invoice | 25 | 19 | 0 | 0 | 0 | 6 |
| ar_dd_represented_transfer | 18 | 9 | 9 | 0 | 0 | 0 |
| ar_retention | 10 | 4 | 6 | 0 | 0 | 0 |
| unidentified | 9 | 0 | 0 | 0 | 9 | 0 |
| ar_short_bank_charges | 2 | 2 | 0 | 0 | 0 | 0 |
| ar_partial | 36 | 16 | 18 | 0 | 0 | 2 |
| unidentified_lookalike | 10 | 0 | 0 | 0 | 10 | 0 |
| ar_advance_deduction | 10 | 5 | 0 | 0 | 0 | 5 |
| ar_third_party_confirming | 17 | 17 | 0 | 0 | 0 | 0 |
| ar_dd_batch | 5 | 0 | 5 | 0 | 0 | 0 |
| ar_dispute_settlement | 13 | 2 | 11 | 0 | 0 | 0 |
| ar_1to1 | 10 | 8 | 1 | 0 | 0 | 1 |
| ar_third_party_agency | 10 | 8 | 0 | 0 | 0 | 2 |
| ar_third_party_parent | 3 | 2 | 0 | 0 | 0 | 1 |
| ap_duplicate_refund | 2 | 0 | 0 | 0 | 2 | 0 |
| ap_factored | 2 | 2 | 0 | 0 | 0 | 0 |
| ar_new_iban | 1 | 1 | 0 | 0 | 0 | 0 |
| customer_refund | 2 | 0 | 0 | 0 | 2 | 0 |
| ar_retention_release | 1 | 0 | 1 | 0 | 0 | 0 |
| ar_cheque_deposit | 1 | 1 | 0 | 0 | 0 | 0 |
| ap_payment_multi | 1 | 0 | 0 | 1 | 0 | 0 |
| **total** | 2626 | 2124 | 209 | 1 | 23 | 269 |

## 4. Detalle del top 10 por puntos

Los ejemplos son bank_txn_id mal, uno de cada tipo de error por turnos, para `solution/tools/inspect_txn.py <run> <id>`. **Recuperabilidad** de cada movimiento fallado, con el pagador que identifica el propio pipeline (no el del golden; ver `PayerId` en `hr_lib.py`: partidas que el envío asignó, IBAN, nombre/historial de TRAIN): *recall* = hay referencia del golden visible en el texto, o el pagador identificado tiene todas las partidas del golden entre sus abiertas y ≤ 5 en rango; *ambigüedad* = el resto (sin referencia, > 5 candidatos, o pagador no identificado / distinto); *imposible* = alguna partida del golden no estaba abierta en ese momento (duplicado, consumida, devuelta, fuera del ledger).

### ar_ref_noise — 1.50 pts, 691 mal de 14940

- Tipos de error: (a) 637, (b) 3, (e) 51. De los (a), 373 se respondieron sin ninguna partida y 264 con otras partidas.
- Recuperabilidad: (a) recall 134, (b) ambigüedad 557. Fracción recall 19 % → **0.29 puntos recuperables**.
- Pagador que identifica el pipeline (movimientos con partidas en el golden): identificado y correcto 443, identificado y distinto del golden 176, sin pagador 72; por vía: pipeline 316, nombre 278, sin pagador 72, iban 25.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 373/373; `ar:subset_dp_prim_m` 93/693; `v3:ref_partial` 52/52; `ar:subset_dp_prim_l` 43/60; `ar:refplus_dp_prim_m` 19/109.
- Ejemplos: `BT-2025-0092792` (a, ambiguity, ar:var), `BT-2025-0092889` (e, ambiguity, ar:subset_dp_prim_s), `BT-2025-0112613` (b, recall, ar:dup_ref), `BT-2025-0092949` (a, ambiguity, v3rest:cat), `BT-2025-0093013` (e, ambiguity, ar:subset_pfx_prim)

### ar_overpayment — 0.97 pts, 85 mal de 135

- Tipos de error: (a) 85. De los (a), 84 se respondieron sin ninguna partida y 1 con otras partidas.
- Recuperabilidad: (a) recall 48, (b) ambigüedad 37. Fracción recall 56 % → **0.55 puntos recuperables**.
- Pagador que identifica el pipeline (movimientos con partidas en el golden): identificado y correcto 54, identificado y distinto del golden 16, sin pagador 15; por vía: nombre 60, sin pagador 15, iban 9, pipeline 1.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 84/84; `v3:ref_partial` 1/1.
- Ejemplos: `BT-2025-0094982` (a, recall, v3rest:cat), `BT-2025-0096576` (a, recall, v3rest:cat), `BT-2025-0096996` (a, ambiguity, v3rest:cat), `BT-2025-0099340` (a, recall, v3rest:cat), `BT-2025-0099366` (a, ambiguity, v3rest:cat)

### ar_1toN_credit_note — 0.88 pts, 360 mal de 1079

- Tipos de error: (a) 209, (e) 151. De los (a), 130 se respondieron sin ninguna partida y 79 con otras partidas.
- Recuperabilidad: (a) recall 244, (b) ambigüedad 116. Fracción recall 68 % → **0.60 puntos recuperables**.
- Pagador que identifica el pipeline (movimientos con partidas en el golden): identificado y correcto 323, sin pagador 19, identificado y distinto del golden 18; por vía: pipeline 230, nombre 99, sin pagador 19, iban 12.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 130/130; `v3:ref_partial` 42/42; `ar:subset_dp_prim_m` 34/149; `ar:subset_dp_prim_l` 28/45; `v3:ref_retention` 27/27.
- Ejemplos: `BT-2025-0093358` (a, ambiguity, v3rest:cat), `BT-2025-0092637` (e, recall, v3:ref_retention), `BT-2025-0094102` (a, recall, ar:subset_dp_prim_l), `BT-2025-0092711` (e, ambiguity, v3:party_comb3), `BT-2025-0094381` (a, recall, v3:ref_partial)

### ar_short_dispute — 0.80 pts, 71 mal de 348

- Tipos de error: (a) 71. De los (a), 62 se respondieron sin ninguna partida y 9 con otras partidas.
- Recuperabilidad: (a) recall 56, (b) ambigüedad 15. Fracción recall 79 % → **0.63 puntos recuperables**.
- Pagador que identifica el pipeline (movimientos con partidas en el golden): identificado y correcto 59, sin pagador 11, identificado y distinto del golden 1; por vía: nombre 48, sin pagador 11, pipeline 9, iban 3.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 62/62; `ar:subset_dp_prim_l` 6/6; `ar:subset_dp_prim_m` 2/2; `v3:party_bank_charges` 1/1.
- Ejemplos: `BT-2025-0093274` (a, recall, v3rest:cat), `BT-2025-0093286` (a, recall, v3rest:cat), `BT-2025-0093640` (a, ambiguity, v3rest:cat), `BT-2025-0094602` (a, ambiguity, v3rest:cat), `BT-2025-0096749` (a, recall, ar:subset_dp_prim_l)

### ar_n_to_m — 0.54 pts, 368 mal de 600

- Tipos de error: (a) 357, (b) 2, (e) 9. De los (a), 311 se respondieron sin ninguna partida y 46 con otras partidas.
- Recuperabilidad: (a) recall 180, (b) ambigüedad 188. Fracción recall 49 % → **0.26 puntos recuperables**.
- Pagador que identifica el pipeline (movimientos con partidas en el golden): identificado y correcto 273, identificado y distinto del golden 49, sin pagador 46; por vía: nombre 239, pipeline 57, sin pagador 46, iban 26.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 311/311; `ar:subset_dp_prim_m` 24/25; `ar:subset_dp_prim_l` 8/8; `v3:ref_partial` 6/6; `ar:refplus_dp_prim_m` 4/7.
- Ejemplos: `BT-2025-0092933` (a, ambiguity, v3rest:cat), `BT-2025-0108496` (e, recall, ar:refplus_dp_prim_s), `BT-2025-0126118` (b, recall, v3:party_comb2), `BT-2025-0092943` (a, ambiguity, v3rest:cat), `BT-2025-0127605` (e, recall, v3:ref_overpay)

### ar_1toN_text — 0.34 pts, 135 mal de 2048

- Tipos de error: (a) 119, (e) 16. De los (a), 61 se respondieron sin ninguna partida y 58 con otras partidas.
- Recuperabilidad: (a) recall 126, (b) ambigüedad 9. Fracción recall 93 % → **0.32 puntos recuperables**.
- Pagador que identifica el pipeline (movimientos con partidas en el golden): identificado y correcto 113, sin pagador 14, identificado y distinto del golden 8; por vía: pipeline 74, nombre 44, sin pagador 14, iban 3.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 61/61; `ar:subset_dp_prim_l` 42/56; `ar:ref_exact` 10/164; `ar:subset_dp_prim_m` 9/52; `ar:refplus_dp_prim_m` 8/78.
- Ejemplos: `BT-2025-0092839` (a, recall, ar:subset_dp_prim_m), `BT-2025-0092966` (e, recall, ar:refplus_dp_prim_m), `BT-2025-0093130` (a, recall, v3rest:cat), `BT-2025-0093325` (e, recall, ar:ref_exact), `BT-2025-0093307` (a, recall, v3rest:cat)

### ar_fx — 0.28 pts, 70 mal de 263

- Tipos de error: (a) 69, (e) 1. De los (a), 22 se respondieron sin ninguna partida y 47 con otras partidas.
- Recuperabilidad: (a) recall 29, (b) ambigüedad 41. Fracción recall 41 % → **0.12 puntos recuperables**.
- Pagador que identifica el pipeline (movimientos con partidas en el golden): identificado y correcto 47, identificado y distinto del golden 12, sin pagador 11; por vía: pipeline 45, nombre 14, sin pagador 11.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3:party_exact` 25/58; `v3rest:cat` 22/22; `ar:subset_dp_prim_m` 9/14; `ar:subset_dp_prim_l` 4/7; `v3:party_duplicate` 3/3.
- Ejemplos: `BT-2025-0092753` (a, ambiguity, v3:party_exact), `BT-2025-0142795` (e, recall, ar:refplus_dp_prim_s), `BT-2025-0092995` (a, ambiguity, v3rest:cat), `BT-2025-0093844` (a, recall, ar:subset_dp_prim_l), `BT-2025-0093846` (a, recall, v3:party_exact)

### ar_discount_late — 0.18 pts, 86 mal de 269

- Tipos de error: (a) 84, (b) 2. De los (a), 54 se respondieron sin ninguna partida y 30 con otras partidas.
- Recuperabilidad: (a) recall 48, (b) ambigüedad 38. Fracción recall 56 % → **0.10 puntos recuperables**.
- Pagador que identifica el pipeline (movimientos con partidas en el golden): identificado y correcto 79, identificado y distinto del golden 7; por vía: nombre 34, pipeline 32, iban 20.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 54/54; `ar:subset_dp_prim_m` 13/13; `ar:subset_dp_prim_l` 8/8; `ar:var` 6/10; `ar:refplus_dp_prim_m` 3/3.
- Ejemplos: `BT-2025-0093035` (a, recall, v3rest:cat), `BT-2025-0127559` (b, ambiguity, remittance), `BT-2025-0093248` (a, ambiguity, ar:var), `BT-2025-0138072` (b, recall, remittance), `BT-2025-0093450` (a, recall, v3rest:cat)

### ar_short_withholding — 0.17 pts, 40 mal de 491

- Tipos de error: (a) 32, (b) 2, (e) 6. De los (a), 26 se respondieron sin ninguna partida y 6 con otras partidas.
- Recuperabilidad: (a) recall 21, (b) ambigüedad 19. Fracción recall 52 % → **0.09 puntos recuperables**.
- Pagador que identifica el pipeline (movimientos con partidas en el golden): identificado y correcto 24, sin pagador 14, identificado y distinto del golden 2; por vía: sin pagador 14, pipeline 14, nombre 12.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 26/26; `v3:ref_partial` 7/7; `ar:subset_dp_prim_m` 4/4; `v3:party_exact` 1/1; `ar:frac_partial` 1/1.
- Ejemplos: `BT-2025-0094782` (a, recall, v3rest:cat), `BT-2025-0111567` (e, recall, v3:ref_partial), `BT-2025-0139794` (b, recall, ar:frac_partial), `BT-2025-0095307` (a, recall, v3rest:cat), `BT-2025-0113870` (e, recall, v3:ref_partial)

### ar_duplicate_payment — 0.17 pts, 33 mal de 228

- Tipos de error: (a) 32, (b) 1. De los (a), 32 se respondieron sin ninguna partida y 0 con otras partidas.
- Recuperabilidad: (c) imposible 33. Fracción recall 0 % → **0.00 puntos recuperables**.
- Pagador que identifica el pipeline (movimientos con partidas en el golden): identificado y correcto 18, identificado y distinto del golden 10, sin pagador 5; por vía: nombre 27, sin pagador 5, pipeline 1.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 32/32; `ar:ref_exact` 1/1.
- Ejemplos: `BT-2025-0093138` (a, impossible, v3rest:cat), `BT-2025-0145274` (b, impossible, ar:ref_exact), `BT-2025-0094261` (a, impossible, v3rest:cat), `BT-2025-0097156` (a, impossible, v3rest:cat), `BT-2025-0097393` (a, impossible, v3rest:cat)

## 5. Siguiente experimento

Dificultad estimada (heurística, no medida): **baja** si ≥ 50 % de los fallos son de excepción/importe/categoría (b/c/d) o ≥ 60 % son recall; **alta** si ≥ 60 % son ambigüedad o imposibles; **media** el resto. *Puntos recuperables* = puntos × fracción (a) recall. Prioridad = puntos recuperables × (1 / 0,5 / 0,25 para baja / media / alta).

| # | segmento | puntos | recuperables | tipo dominante | recall / ambig. / imposible | dificultad | prioridad | qué probar |
|---:|---|---:|---:|---|---:|---|---:|---|
| 1 | ar_short_dispute | 0.80 | 0.63 | pares equivocados | 56 / 15 / 0 | baja | 0.63 | 100 % son «pares equivocados» (pase principal `v3rest:cat`, 62 fallos); domina el recall (referencia visible o ≤ 5 candidatos del pagador): ampliar la cobertura del matcher. |
| 2 | ar_1toN_credit_note | 0.88 | 0.60 | pares equivocados | 244 / 116 / 0 | baja | 0.60 | 58 % son «pares equivocados» (pase principal `v3rest:cat`, 130 fallos); domina el recall (referencia visible o ≤ 5 candidatos del pagador): ampliar la cobertura del matcher. |
| 3 | ar_1toN_text | 0.34 | 0.32 | pares equivocados | 126 / 9 / 0 | baja | 0.32 | 88 % son «pares equivocados» (pase principal `v3rest:cat`, 61 fallos); domina el recall (referencia visible o ≤ 5 candidatos del pagador): ampliar la cobertura del matcher. |
| 4 | ar_overpayment | 0.97 | 0.55 | pares equivocados | 48 / 37 / 0 | media | 0.27 | 100 % son «pares equivocados» (pase principal `v3rest:cat`, 84 fallos); domina el recall (referencia visible o ≤ 5 candidatos del pagador): ampliar la cobertura del matcher. |
| 5 | ar_n_to_m | 0.54 | 0.26 | pares equivocados | 180 / 188 / 0 | media | 0.13 | 97 % son «pares equivocados» (pase principal `v3rest:cat`, 311 fallos); domina la ambigüedad (sin referencia y muchos candidatos, o pagador no identificado): hace falta un criterio de selección entre candidatos o mejor identificación de pagador, no más cobertura. |
| 6 | ar_ref_noise | 1.50 | 0.29 | pares equivocados | 134 / 557 / 0 | alta | 0.07 | 92 % son «pares equivocados» (pase principal `v3rest:cat`, 373 fallos); domina la ambigüedad (sin referencia y muchos candidatos, o pagador no identificado): hace falta un criterio de selección entre candidatos o mejor identificación de pagador, no más cobertura. |
| 7 | ar_fx | 0.28 | 0.12 | pares equivocados | 29 / 41 / 0 | media | 0.06 | 99 % son «pares equivocados» (pase principal `v3:party_exact`, 25 fallos); domina la ambigüedad (sin referencia y muchos candidatos, o pagador no identificado): hace falta un criterio de selección entre candidatos o mejor identificación de pagador, no más cobertura. |
| 8 | ar_discount_late | 0.18 | 0.10 | pares equivocados | 48 / 38 / 0 | media | 0.05 | 98 % son «pares equivocados» (pase principal `v3rest:cat`, 54 fallos); domina el recall (referencia visible o ≤ 5 candidatos del pagador): ampliar la cobertura del matcher. |
| 9 | ar_short_withholding | 0.17 | 0.09 | pares equivocados | 21 / 19 / 0 | media | 0.04 | 80 % son «pares equivocados» (pase principal `v3rest:cat`, 26 fallos); domina el recall (referencia visible o ≤ 5 candidatos del pagador): ampliar la cobertura del matcher. |
| 10 | ar_duplicate_payment | 0.17 | 0.00 | pares equivocados | 0 / 0 / 33 | alta | 0.00 | 97 % son «pares equivocados» (pase principal `v3rest:cat`, 32 fallos); casi todo son partidas que no estaban abiertas en ese momento (duplicados, devoluciones, consumidas antes): hace falta modelar el estado del ledger, no más cobertura. |

