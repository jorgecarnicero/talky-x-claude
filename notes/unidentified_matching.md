# UNIDENTIFIED: diagnóstico sobre la línea base v6a (dev, ventana 2025-04..05, score 93,79)

Solo lectura: no se ha implementado nada. Detalle por línea en `notes/unidentified_matching_1121.csv` (local: .gitignore excluye los CSV, contiene textos bancarios de TRAIN)
(bank_txn_id, pagador identificado, nº de ARI abiertas del cliente candidato, IBAN/referencia visible, partidas del golden).

## Cifras medidas
- Clase UNIDENTIFIED: 2739 filas emitidas, 1618 correctas (P 0,591, R 0,988, F1 0,739).
- Falsos negativos: 20 filas (~0,00 puntos). Ignorables.
- Falsos positivos: **1121**, todas con partidas en el golden. 1120 salen del fallback `v3rest:cat` y 1 de `v3:party_cn_exact`.
  La clase falla por el matching, no por la regla de emisión.
- Por lo que ve el pipeline en esas 1121: 19 sin pagador identificado; 56 con pagador pero sin ARI abierta en su divisa;
  **1046 con pagador identificado y candidatas abiertas** (cliente top1 = el del golden en 854 de 1102 identificadas, top3 en 967;
  el conjunto del golden cabe en las abiertas del top3 en 657).
- Evidencia: 329 con IBAN o referencia visible (0 TP con esa evidencia) y 773 solo por nombre.
- 471 de las 1102 identificadas llevan además una excepción (PARTIAL/SHORT_*/FX) en el golden.
- Los TP con pagador identificado por nombre (672) tienen mediana 1 ARI abierta; los FP, mediana 26.
- Aproximación: el libro abierto se reconstruye reproduciendo el sub base por fecha, no el orden real de pases.

## Por qué falla "emitir la mejor candidata"
Simulado con el scorer oficial sobre el sub base modificado:
| política | score | delta |
|---|---:|---:|
| puerta (IBAN/ref o ≥6 abiertas) + mejor candidata FIFO | 93,67 | -0,12 |
| solo IBAN/ref + FIFO | 93,78 | -0,01 |
| todo identificado + FIFO | 93,05 | -0,74 |
| subset-sum exacto (más antigua) | 93,68–93,75 | -0,04..-0,11 |
Acierto de pares 0,2–0,35 (0,03–0,10 en los casos débiles); líneas completas bien ≈ 0 de 1028. El subset-sum exacto da solución
en 341 casos, pero 270 tienen varias y elegir una acierta ~12 %. El pago real suele ser un conjunto de 2–45 facturas, que ninguna
heurística ciega acierta; baja la precisión de asignación y mete falsos PARTIAL.

## Decisión
- No se emite categoría alternativa (p. ej. CUSTOMER_ADVANCE): subía +0,12..+0,31 pero es una respuesta inexacta (palanca de métrica).
- No se emite la mejor candidata por FIFO (baja la nota).
- La mejora real exige cerrar el conjunto de partidas en el matching (ar_multi / ar_multi_late / ar_match), fuera de esta zona.
  Techo teórico de la clase en filas: 0,57 puntos (3,89 en líneas).
