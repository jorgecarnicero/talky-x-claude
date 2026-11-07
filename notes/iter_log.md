# Registro de iteraciones (model/v6d-ref, base bc030cc, dev 94.33)

## Ronda 1 — ar_ref_noise (16:00–16:11) — DESCARTADA
- Diagnóstico (dev_runs/overpay vs golden, 674 movimientos mal de 14 940):
  (iii) pagador no identificado → UNIDENTIFIED por v3rest: 352; (iii') pagador equivocado (v3/ar): 142;
  (ii) pagador correcto, conjunto distinto (sobre todo ar_match): 178; (i) omisión pura (subconjunto del golden): 0;
  regla de importe/excepción con items correctos: 2. (iv) ambigüedad: no separable offline; las 352 suelen citar solo
  parte de las facturas (referencias truncadas o ninguna), así que el conjunto no queda determinado por el texto.
- Hipótesis S1: cada token numérico ≥5 dígitos del texto/remittance que corresponde a UNA factura (misma entidad y
  divisa, nº de documento que empieza o termina por el token) se reclama si la suma de esas facturas = importe exacto.
- Simulación offline (puntuador de líneas = criterio oficial de línea completa):
  - solo líneas pendientes (antes de v3rest): +6 / −0, ninguna en ar_ref_noise;
  - antes de v3import (también sobre v3/ar): +15 / −75 (pisa ar_duplicate_payment: 72).
- Decisión: < 30 líneas netas → descartada sin ejecutar dev.py. Nota dev sin cambio (94.33).
- Recuperables sin ambigüedad en ar_ref_noise con referencia + suma exacta: ~0. La mejora exige cerrar el conjunto
  en el matcher (ar_match) o identificar el pagador, no reglas por referencia.

## Ronda 2 — ar_n_to_m (16:11–16:14) — DESCARTADA en diagnóstico
- ar_1toN_credit_note no se ataca: ar_bigpool (origin/model/v6b) ya resuelve lotes de facturas netos de abonos con
  pagador por IBAN/alias exacto; duplicarlo no aporta.
- ar_n_to_m: 600 movimientos, 253 mal: 174 UNIDENTIFIED (v3rest), 35+8 pagador correcto/conjunto distinto, 23 items
  correctos con importe/excepción mal, 12 pagador equivocado. PARTIAL del golden = 0,4/0,5/0,6 del total (290 filas),
  justo las fracciones que ya usa ar_multi_late.
- Las 174 UNIDENTIFIED (cota superior de facturas del pagador en su divisa): con PARTIAL 21 (pool ≤12), 39 (13–25),
  47 (>25); sin PARTIAL 8 / 19 / 40. Las de pool ≤12 caen por no identificar el pagador (ar_multi_late: no_payer 2228).
- Hipótesis valorada: subir MAX_POOL de ar_multi_late a ~16. Cota superior <39 líneas antes de ambigüedad (más
  subconjuntos con la misma suma al crecer el pool), sin simulación fiel posible offline (requiere Matcher y libro
  abierto real). Estimación < 30 netas → descartada sin ejecutar dev.py. Nota dev sin cambio (94.33).
- Siguiente palanca real: identificación de pagador (no_payer) para ar_multi_late/ar_bigpool, no fracciones ni pools.
