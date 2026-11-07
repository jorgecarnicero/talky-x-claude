# Headroom of run `base`

- Score del run: **93.79** (result.json: 93.79) → faltan **6.21** puntos hasta 100.
- Ventana: 2025-04-01..2025-05-31, 69553 movimientos, dataset `/Users/mateolarareguera/hackdata/data_dev_exc`.
- Método: se reconstruye el envío del run con las filas de un segmento (o clase) sustituidas por el golden y se repuntúa con el scorer oficial (`starter/bankrec_score.py`); puntos = score nuevo − score del run. Cifras exactas, no aproximadas.
- Comprobaciones hechas por la herramienta: el envío sin modificar reproduce 93.79; sustituir todos los segmentos da 100.00.

## 1. Margen por segmento

Puntos que sube el score si los movimientos del segmento pasan a ser exactamente el golden (segmentos sin fallos: 0,00, omitidos).

| segmento | movimientos | accuracy actual | mal | puntos | % del gap |
|---|---:|---:|---:|---:|---:|
| ar_ref_noise | 14940 | 0.955 | 674 | 1.49 | 24 % |
| ar_short_dispute | 348 | 0.796 | 71 | 0.79 | 13 % |
| ar_1toN_credit_note | 1079 | 0.756 | 263 | 0.78 | 13 % |
| ar_overpayment | 135 | 0.533 | 63 | 0.65 | 10 % |
| ar_n_to_m | 600 | 0.578 | 253 | 0.38 | 6 % |
| ar_1toN_text | 2048 | 0.933 | 137 | 0.34 | 5 % |
| ar_fx | 263 | 0.734 | 70 | 0.27 | 4 % |
| ar_discount_late | 269 | 0.680 | 86 | 0.18 | 3 % |
| ar_short_withholding | 491 | 0.919 | 40 | 0.17 | 3 % |
| ar_duplicate_payment | 228 | 0.860 | 32 | 0.16 | 3 % |
| ar_short_discount | 2355 | 0.949 | 120 | 0.15 | 2 % |
| ap_fx | 411 | 0.993 | 3 | 0.12 | 2 % |
| ap_confirming | 261 | 0.843 | 41 | 0.10 | 2 % |
| ar_1toN_remittance | 381 | 0.971 | 11 | 0.08 | 1 % |
| ar_same_amount_fifo | 2024 | 0.962 | 77 | 0.05 | 1 % |
| ar_dd_returned | 1214 | 0.978 | 27 | 0.05 | 1 % |
| ar_instalment | 1497 | 0.955 | 68 | 0.04 | 1 % |
| unidentified | 1160 | 0.991 | 11 | 0.04 | 1 % |
| ar_short_bank_charges | 51 | 0.961 | 2 | 0.04 | 1 % |
| ar_dd_represented_transfer | 311 | 0.955 | 14 | 0.03 | 0 % |
| ar_partial | 1333 | 0.982 | 24 | 0.02 | 0 % |
| ar_third_party_confirming | 293 | 0.942 | 17 | 0.02 | 0 % |
| ar_same_amount_month | 1422 | 0.992 | 12 | 0.02 | 0 % |
| unidentified_lookalike | 478 | 0.981 | 9 | 0.02 | 0 % |
| ar_replaced_invoice | 82 | 0.902 | 8 | 0.02 | 0 % |
| ar_dispute_settlement | 373 | 0.965 | 13 | 0.01 | 0 % |
| ar_1to1 | 6725 | 0.999 | 9 | 0.01 | 0 % |
| ar_third_party_agency | 123 | 0.927 | 9 | 0.01 | 0 % |
| ar_dd_batch | 540 | 0.991 | 5 | 0.01 | 0 % |
| ar_retention | 304 | 0.984 | 5 | 0.00 | 0 % |
| ar_retention_release | 257 | 0.992 | 2 | 0.00 | 0 % |
| customer_refund | 126 | 0.984 | 2 | 0.00 | 0 % |
| ar_third_party_parent | 51 | 0.961 | 2 | 0.00 | 0 % |
| ap_duplicate_refund | 26 | 0.923 | 2 | 0.00 | 0 % |
| ap_factored | 477 | 0.996 | 2 | 0.00 | 0 % |
| ar_new_iban | 102 | 0.990 | 1 | 0.00 | 0 % |
| ar_cheque_deposit | 675 | 0.999 | 1 | 0.00 | 0 % |
| ap_payment_multi | 8323 | 1.000 | 1 | 0.00 | 0 % |

**Coherencia**: suma de márgenes por segmento = **6.05** frente a **6.21** que faltan (100 − 93.79); diferencia +0.16. No coinciden exactamente porque el score no es lineal (F1 de asignación y macro-F1 de excepciones son cocientes): cada segmento se mide aislado, con el resto tal como está, mientras que corregirlos todos a la vez da exactamente 100. Los movimientos mal suman 2187 (3.14 % de 69553), que por sí solo vale 0.94 puntos del término de accuracy (peso 0,3).

## 2. Margen por clase de excepción

«Filas»: se sustituyen solo las filas de esa clase (las enviadas y las del golden) y el resto del envío queda igual. «Líneas»: se sustituyen por el golden todos los movimientos donde esa clase aparece en el envío o en el golden (incluye el efecto sobre accuracy del movimiento). «A F1 0,9»: puntos del término macro-F1 si la clase llegara a F1 0,9 (= 100·0,2·(0,9 − F1)/10 clases con soporte; 0 si ya la supera).

| clase | F1 actual | soporte | filas enviadas | puntos (filas) | puntos (líneas) | puntos a F1 0,9 | nota |
|---|---:|---:|---:|---:|---:|---:|---|
| SHORT_DISPUTE | 0.920 | 976 | 831 | 0.77 | 0.80 | 0.00 |  |
| OVERPAYMENT | 0.654 | 135 | 85 | 0.74 | 0.75 | 0.49 |  |
| PARTIAL | 0.893 | 4376 | 4513 | 0.73 | 1.10 | 0.01 |  |
| FX_DIFFERENCE | 0.853 | 5726 | 6181 | 0.62 | 0.75 | 0.09 |  |
| UNIDENTIFIED | 0.739 | 1638 | 2739 | 0.57 | 3.89 | 0.32 |  |
| SHORT_BANK_CHARGES | 0.855 | 52 | 65 | 0.29 | 0.30 | 0.09 | (n pequeño) |
| DUPLICATE_PAYMENT | 0.891 | 249 | 238 | 0.24 | 0.26 | 0.02 |  |
| SHORT_DISCOUNT | 0.969 | 5997 | 5638 | 0.21 | 0.28 | 0.00 |  |
| SHORT_WITHHOLDING | 0.939 | 1340 | 1187 | 0.16 | 0.17 | 0.00 |  |
| RETURNED | 0.981 | 1478 | 1480 | 0.05 | 0.05 | 0.00 |  |

## 3. Taxonomía de los movimientos mal, por segmento

Cada movimiento mal cae en un único tipo, por este orden: (d) el golden no tiene partidas y la respuesta difiere; (a) se respondió sin partidas o con un conjunto de partidas que no contiene ni está contenido en el golden; (e) el conjunto enviado es subconjunto/superconjunto estricto del golden; con el mismo conjunto: (b) alguna excepción difiere, si no (c) algún importe difiere más allá de la tolerancia.

| segmento | mal | (a) pares | (b) excepción | (c) importe | (d) categoría | (e) filas +/− |
|---|---:|---:|---:|---:|---:|---:|
| ar_ref_noise | 674 | 622 | 2 | 0 | 0 | 50 |
| ar_short_dispute | 71 | 71 | 0 | 0 | 0 | 0 |
| ar_1toN_credit_note | 263 | 205 | 0 | 0 | 0 | 58 |
| ar_overpayment | 63 | 63 | 0 | 0 | 0 | 0 |
| ar_n_to_m | 253 | 217 | 23 | 0 | 0 | 13 |
| ar_1toN_text | 137 | 121 | 0 | 0 | 0 | 16 |
| ar_fx | 70 | 69 | 0 | 0 | 0 | 1 |
| ar_discount_late | 86 | 84 | 2 | 0 | 0 | 0 |
| ar_short_withholding | 40 | 32 | 2 | 0 | 0 | 6 |
| ar_duplicate_payment | 32 | 32 | 0 | 0 | 0 | 0 |
| ar_short_discount | 120 | 105 | 12 | 0 | 0 | 3 |
| ap_fx | 3 | 2 | 0 | 0 | 0 | 1 |
| ap_confirming | 41 | 41 | 0 | 0 | 0 | 0 |
| ar_1toN_remittance | 11 | 11 | 0 | 0 | 0 | 0 |
| ar_same_amount_fifo | 77 | 77 | 0 | 0 | 0 | 0 |
| ar_dd_returned | 27 | 27 | 0 | 0 | 0 | 0 |
| ar_instalment | 68 | 7 | 59 | 0 | 0 | 2 |
| unidentified | 11 | 0 | 0 | 0 | 11 | 0 |
| ar_short_bank_charges | 2 | 2 | 0 | 0 | 0 | 0 |
| ar_dd_represented_transfer | 14 | 9 | 5 | 0 | 0 | 0 |
| ar_partial | 24 | 15 | 9 | 0 | 0 | 0 |
| ar_third_party_confirming | 17 | 17 | 0 | 0 | 0 | 0 |
| ar_same_amount_month | 12 | 12 | 0 | 0 | 0 | 0 |
| unidentified_lookalike | 9 | 0 | 0 | 0 | 9 | 0 |
| ar_replaced_invoice | 8 | 7 | 0 | 0 | 0 | 1 |
| ar_dispute_settlement | 13 | 2 | 11 | 0 | 0 | 0 |
| ar_1to1 | 9 | 7 | 1 | 0 | 0 | 1 |
| ar_third_party_agency | 9 | 8 | 0 | 0 | 0 | 1 |
| ar_dd_batch | 5 | 0 | 5 | 0 | 0 | 0 |
| ar_retention | 5 | 2 | 3 | 0 | 0 | 0 |
| ar_retention_release | 2 | 0 | 2 | 0 | 0 | 0 |
| customer_refund | 2 | 0 | 0 | 0 | 2 | 0 |
| ar_third_party_parent | 2 | 1 | 0 | 0 | 0 | 1 |
| ap_duplicate_refund | 2 | 0 | 0 | 0 | 2 | 0 |
| ap_factored | 2 | 2 | 0 | 0 | 0 | 0 |
| ar_new_iban | 1 | 1 | 0 | 0 | 0 | 0 |
| ar_cheque_deposit | 1 | 1 | 0 | 0 | 0 | 0 |
| ap_payment_multi | 1 | 0 | 0 | 1 | 0 | 0 |
| **total** | 2187 | 1872 | 136 | 1 | 24 | 154 |

## 4. Detalle del top 10 por puntos

Los ejemplos son bank_txn_id mal, uno de cada tipo de error por turnos, para `inspect_txn.py` (esa herramienta no existe aún en este checkout). Recall = el golden estaba abierto y había referencia visible o ≤ 5 candidatos en rango; ambigüedad = abierto, sin referencia y > 5 candidatos; no localizable = alguna partida no estaba abierta en ese momento (mismo método que `notes/bolsa_*.md`, ver `solution/tools/bolsa.py`).

### ar_ref_noise — 1.49 pts, 674 mal de 14940

- Tipos de error: (a) 622, (b) 2, (e) 50. De los (a), 354 se respondieron sin ninguna partida (no encontrado) y 268 con otras partidas.
- Fallo de: recall 242, ambigüedad 432.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 354/354; `ar:subset_dp_prim_m` 89/690; `v3:ref_partial` 51/51; `ar:subset_dp_prim_l` 44/61; `ar:var` 19/19.
- Ejemplos: `BT-2025-0092792` (a, ambiguity, ar:var), `BT-2025-0092889` (e, ambiguity, ar:subset_dp_prim_s), `BT-2025-0128753` (b, ambiguity, v3:party_duplicate), `BT-2025-0092949` (a, ambiguity, v3rest:cat), `BT-2025-0093013` (e, ambiguity, ar:subset_pfx_prim)

### ar_short_dispute — 0.79 pts, 71 mal de 348

- Tipos de error: (a) 71. De los (a), 62 se respondieron sin ninguna partida (no encontrado) y 9 con otras partidas.
- Fallo de: recall 56, ambigüedad 15.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 62/62; `ar:subset_dp_prim_l` 6/6; `ar:subset_dp_prim_m` 2/2; `v3:party_bank_charges` 1/1.
- Ejemplos: `BT-2025-0093274` (a, recall, v3rest:cat), `BT-2025-0093286` (a, recall, v3rest:cat), `BT-2025-0093640` (a, ambiguity, v3rest:cat), `BT-2025-0094602` (a, ambiguity, v3rest:cat), `BT-2025-0096749` (a, recall, ar:subset_dp_prim_l)

### ar_1toN_credit_note — 0.78 pts, 263 mal de 1079

- Tipos de error: (a) 205, (e) 58. De los (a), 129 se respondieron sin ninguna partida (no encontrado) y 76 con otras partidas.
- Fallo de: recall 149, ambigüedad 114.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 129/129; `ar:subset_dp_prim_m` 32/146; `ar:subset_dp_prim_l` 28/48; `ar:refplus_dp_prim_m` 16/54; `ar:ref_exact` 8/83.
- Ejemplos: `BT-2025-0093358` (a, ambiguity, v3rest:cat), `BT-2025-0092711` (e, ambiguity, v3:party_comb3), `BT-2025-0094102` (a, recall, ar:subset_dp_prim_l), `BT-2025-0096941` (e, ambiguity, v3:party_comb2), `BT-2025-0094381` (a, recall, v3:ref_partial)

### ar_overpayment — 0.65 pts, 63 mal de 135

- Tipos de error: (a) 63. De los (a), 61 se respondieron sin ninguna partida (no encontrado) y 2 con otras partidas.
- Fallo de: recall 51, ambigüedad 12.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 61/61; `ar:nm` 1/1; `v3:ref_partial` 1/1.
- Ejemplos: `BT-2025-0096576` (a, recall, v3rest:cat), `BT-2025-0096996` (a, recall, v3rest:cat), `BT-2025-0099340` (a, recall, v3rest:cat), `BT-2025-0099366` (a, ambiguity, v3rest:cat), `BT-2025-0103976` (a, recall, v3rest:cat)

### ar_n_to_m — 0.38 pts, 253 mal de 600

- Tipos de error: (a) 217, (b) 23, (e) 13. De los (a), 175 se respondieron sin ninguna partida (no encontrado) y 42 con otras partidas.
- Fallo de: recall 118, ambigüedad 135.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 175/175; `ar:subset_dp_prim_m` 21/24; `ar:nm` 15/47; `ar:nm_ref` 12/62; `ar:subset_dp_prim_l` 7/7.
- Ejemplos: `BT-2025-0092933` (a, ambiguity, v3rest:cat), `BT-2025-0115827` (b, ambiguity, ar:nm), `BT-2025-0096186` (e, ambiguity, ar:nm), `BT-2025-0093086` (a, ambiguity, v3rest:cat), `BT-2025-0126118` (b, recall, v3:party_comb2)

### ar_1toN_text — 0.34 pts, 137 mal de 2048

- Tipos de error: (a) 121, (e) 16. De los (a), 58 se respondieron sin ninguna partida (no encontrado) y 63 con otras partidas.
- Fallo de: recall 128, ambigüedad 9.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 58/58; `ar:subset_dp_prim_l` 42/56; `ar:subset_dp_prim_m` 12/55; `ar:ref_exact` 10/162; `ar:refplus_dp_prim_m` 7/77.
- Ejemplos: `BT-2025-0092839` (a, recall, ar:subset_dp_prim_m), `BT-2025-0092966` (e, recall, ar:refplus_dp_prim_m), `BT-2025-0093130` (a, recall, v3rest:cat), `BT-2025-0093325` (e, recall, ar:ref_exact), `BT-2025-0093307` (a, recall, v3rest:cat)

### ar_fx — 0.27 pts, 70 mal de 263

- Tipos de error: (a) 69, (e) 1. De los (a), 21 se respondieron sin ninguna partida (no encontrado) y 48 con otras partidas.
- Fallo de: recall 29, ambigüedad 41.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3:party_exact` 25/58; `v3rest:cat` 21/21; `ar:subset_dp_prim_m` 10/15; `ar:subset_dp_prim_l` 3/6; `v3:party_duplicate` 3/3.
- Ejemplos: `BT-2025-0092753` (a, ambiguity, v3:party_exact), `BT-2025-0142795` (e, recall, ar:refplus_dp_prim_s), `BT-2025-0092995` (a, ambiguity, v3rest:cat), `BT-2025-0093844` (a, recall, ar:subset_dp_prim_l), `BT-2025-0093846` (a, recall, v3:party_exact)

### ar_discount_late — 0.18 pts, 86 mal de 269

- Tipos de error: (a) 84, (b) 2. De los (a), 56 se respondieron sin ninguna partida (no encontrado) y 28 con otras partidas.
- Fallo de: recall 48, ambigüedad 38.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 56/56; `ar:subset_dp_prim_m` 12/12; `ar:subset_dp_prim_l` 7/7; `ar:var` 6/10; `ar:refplus_dp_prim_m` 3/3.
- Ejemplos: `BT-2025-0093035` (a, recall, v3rest:cat), `BT-2025-0127559` (b, ambiguity, remittance), `BT-2025-0093248` (a, ambiguity, ar:var), `BT-2025-0138072` (b, recall, remittance), `BT-2025-0093450` (a, recall, v3rest:cat)

### ar_short_withholding — 0.17 pts, 40 mal de 491

- Tipos de error: (a) 32, (b) 2, (e) 6. De los (a), 26 se respondieron sin ninguna partida (no encontrado) y 6 con otras partidas.
- Fallo de: recall 22, ambigüedad 18.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 26/26; `v3:ref_partial` 7/7; `ar:subset_dp_prim_m` 4/4; `v3:party_exact` 1/1; `ar:frac_partial` 1/1.
- Ejemplos: `BT-2025-0094782` (a, recall, v3rest:cat), `BT-2025-0111567` (e, recall, v3:ref_partial), `BT-2025-0139794` (b, recall, ar:frac_partial), `BT-2025-0095307` (a, recall, v3rest:cat), `BT-2025-0113870` (e, recall, v3:ref_partial)

### ar_duplicate_payment — 0.16 pts, 32 mal de 228

- Tipos de error: (a) 32. De los (a), 32 se respondieron sin ninguna partida (no encontrado) y 0 con otras partidas.
- Fallo de: no localizable 32.
- Pase que los resuelve (fallos / movimientos del segmento por pase): `v3rest:cat` 32/32.
- Ejemplos: `BT-2025-0093138` (a, not findable, v3rest:cat), `BT-2025-0094261` (a, not findable, v3rest:cat), `BT-2025-0097156` (a, not findable, v3rest:cat), `BT-2025-0097393` (a, not findable, v3rest:cat), `BT-2025-0099627` (a, not findable, v3rest:cat)

## 5. Siguiente experimento

Dificultad estimada (heurística, no medida): **baja** si ≥ 50 % de los fallos son de excepción/importe/categoría (b/c/d) o ≥ 60 % son recall; **alta** si ≥ 60 % son ambigüedad o no localizables; **media** el resto. Prioridad = puntos × (1 / 0,5 / 0,25 para baja / media / alta).

| # | segmento | puntos | tipo dominante | recall / ambigüedad | dificultad | prioridad | qué probar |
|---:|---|---:|---|---:|---|---:|---|
| 1 | ar_short_dispute | 0.79 | pares equivocados | 56 / 15 | baja | 0.79 | 100 % de sus fallos son «pares equivocados» (matcher principal `v3rest:cat`, 62 fallos); la mayoría es recall (referencia visible o pocos candidatos): ampliar la cobertura del matcher de referencias. |
| 2 | ar_overpayment | 0.65 | pares equivocados | 51 / 12 | baja | 0.65 | 100 % de sus fallos son «pares equivocados» (matcher principal `v3rest:cat`, 61 fallos); la mayoría es recall (referencia visible o pocos candidatos): ampliar la cobertura del matcher de referencias. |
| 3 | ar_1toN_credit_note | 0.78 | pares equivocados | 149 / 114 | media | 0.39 | 78 % de sus fallos son «pares equivocados» (matcher principal `v3rest:cat`, 129 fallos); la mayoría es recall (referencia visible o pocos candidatos): ampliar la cobertura del matcher de referencias. |
| 4 | ar_ref_noise | 1.49 | pares equivocados | 242 / 432 | alta | 0.37 | 92 % de sus fallos son «pares equivocados» (matcher principal `v3rest:cat`, 354 fallos); la mayoría es ambigüedad (varios candidatos y sin referencia): hace falta un criterio de selección entre candidatos (importes, fechas, FIFO), no más cobertura. |
| 5 | ar_1toN_text | 0.34 | pares equivocados | 128 / 9 | baja | 0.34 | 88 % de sus fallos son «pares equivocados» (matcher principal `v3rest:cat`, 58 fallos); la mayoría es recall (referencia visible o pocos candidatos): ampliar la cobertura del matcher de referencias. |
| 6 | ar_n_to_m | 0.38 | pares equivocados | 118 / 135 | media | 0.19 | 86 % de sus fallos son «pares equivocados» (matcher principal `v3rest:cat`, 175 fallos); la mayoría es ambigüedad (varios candidatos y sin referencia): hace falta un criterio de selección entre candidatos (importes, fechas, FIFO), no más cobertura. |
| 7 | ar_fx | 0.27 | pares equivocados | 29 / 41 | media | 0.13 | 99 % de sus fallos son «pares equivocados» (matcher principal `v3:party_exact`, 25 fallos); la mayoría es ambigüedad (varios candidatos y sin referencia): hace falta un criterio de selección entre candidatos (importes, fechas, FIFO), no más cobertura. |
| 8 | ar_discount_late | 0.18 | pares equivocados | 48 / 38 | media | 0.09 | 98 % de sus fallos son «pares equivocados» (matcher principal `v3rest:cat`, 56 fallos); la mayoría es recall (referencia visible o pocos candidatos): ampliar la cobertura del matcher de referencias. |
| 9 | ar_short_withholding | 0.17 | pares equivocados | 22 / 18 | media | 0.08 | 80 % de sus fallos son «pares equivocados» (matcher principal `v3rest:cat`, 26 fallos); la mayoría es recall (referencia visible o pocos candidatos): ampliar la cobertura del matcher de referencias. |
| 10 | ar_duplicate_payment | 0.16 | pares equivocados | 0 / 32 | alta | 0.04 | 100 % de sus fallos son «pares equivocados» (matcher principal `v3rest:cat`, 32 fallos); la mayoría son partidas que no estaban abiertas en ese momento (duplicados, devoluciones, consumidas antes): hace falta modelar el estado del ledger, no más cobertura. |

