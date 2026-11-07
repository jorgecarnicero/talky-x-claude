# Bloque ledger: devoluciones, duplicados y reembolsos de proveedor (exploración)

Rama `model/v3-ledger`. Solo exploración; no se ha escrito código de pipeline ni tocado `bankrec.py`.

**Alcance de datos.** Solo movimientos con `booking_date < 2025-01-01` (1.289.944 líneas de TRAIN; las ventanas de dev
2025-04/05 y LIVE no se han leído). DuckDB sobre `~/hackdata/data/_duck/*.parquet`, `train/direct_debit_batches` y
`masters/`. `segment` solo se usa para evaluar (definir verdad/denominadores), nunca como señal de una regla.
Las consultas viven en el scratchpad de la sesión, no en el repo.

Volúmenes pre-2025 (líneas de banco): devoluciones DD 22.885 (≈545/mes) · reembolsos de abono APC 15.280 ·
duplicados AR 3.795 · duplicados AP 425 · reembolso de duplicado a cliente (`CUSTOMER_REFUND`) 2.257 · reembolso de
proveedor de duplicado (`SUPPLIER_REFUND`) 420.

---

## 1. Devoluciones de adeudo directo (`ar_dd_returned`)

### Qué traen

Una fila golden por línea, importe negativo, `exception=RETURNED`, importe = el de la asignación original (coincide
en el 100 % con el importe de la línea y con la asignación original).

| Banco | `bank_code` / `bank_subcode` | Líneas | Texto |
|---|---|---|---|
| HPNB (US) | `PMNT/RDDT/UPDD` / – | 10.758 | `RUECKLASTSCHRIFT AM04 MREF+VTM-US-0010698` (ref1 = id banco) |
| PENA (ES) | `14` / `021` | 4.683 | `DEVOLUCION RECIBO AM04 REM 04231107654 \| MANDATO VTM-ES-… \| <nombre>` o `DEVOL.RECIBOS S/REMESA FECHA \| MANDATO …`; **ref2 = `DEV`+id de lote (11 dígitos) en el 100 %** |
| WATT (AU) | `PMNT/RDDT/UPDD` | 2.219 | igual que HPNB |
| LORV (FR) | `NRTI` | 1.939 | `IMPAYE PRLV AM04 RUM VTM-FR-… <nombre>` |
| RHEI (DE) | `PMNT/RDDT/UPDD` | 1.773 | `RUECKLASTSCHRIFT …` + counterparty_name |
| VALD (IT) | `NRTI` | 855 | `SDD RETURN AM04 MANDATE VTM-IT-… <nombre>` |
| NORT (GB) | `NRTI` | 658 | `SDD RETURN …` |

- **El mandato `VTM-XX-nnnn` está en el texto del 100 % de las devoluciones.** No hay `end_to_end_id` ni
  `remittance_info` en ninguna (camt tampoco los trae en estas líneas). El id de lote solo aparece en PENA (20,5 %).
- Códigos de motivo (irrelevantes para la salida, el `exception` es siempre `RETURNED`): AM04 40 %, MS02 16 %,
  MD06 9 %, AC04 6 %, AC06 5 %, MD01 4 %, MS03 4 %, AC01 3 %; ~10 % sin código (PENA “S/REMESA FECHA”).
- Fiabilidad del `bank_code` como detector: `PMNT/RDDT/UPDD` y PENA `14/021` son 100 % devoluciones DD. `NRTI` es
  compartido con cheques/contracargos/transferencias rechazadas (LORV 91 % DD, NORT 94 %, VALD 95 %, otros bancos 0 %):
  para `NRTI` hace falta el mandato en el texto.

### Cómo se enlaza con el original

La devolución cita **mandato + (a veces) lote**; el original es la fila del fichero `direct_debit_batches` con ese
mandato e importe, cobrada antes de la devolución, y el ingreso del banco que la abonó es el de su `batch_id` (el
`batch_id` de 11 dígitos aparece en el texto del abono: `…EINZUG 02230303340…`).

Desfase devolución − ingreso del lote original (22.753 devoluciones con original en TRAIN):

| Tramo | Devoluciones | % |
|---|---|---|
| 2–14 días | 20.501 | 90,1 % |
| 15–30 | 407 | 1,8 % |
| 31–60 | 1.343 | 5,9 % |
| >60 (máx. 75) | 502 | 2,2 % |

Mediana 7 d, p90 14 d, igual en todos los bancos (RHEI y VALD tienen p90 ≈ 26 d por más devoluciones tardías). Mínimo 2 d.
132 devoluciones (jul–sep 2021) no tienen original en TRAIN: se cobraron antes del inicio del dataset.
696 ítems se devuelven más de una vez (re-presentados y devueltos de nuevo; 22.162 ítems distintos para 22.885 líneas).

Reglas de enlace sin usar golden, % de devoluciones cuyo lote enlazado = lote real (denominador 22.753):

| Regla | Enlazadas | Correctas |
|---|---|---|
| mandato + importe exacto, cobro ≤ fecha devolución, ≤90 d, **la más reciente** | 99,6 % | 95,2 % |
| idem con ventana 1–14 d | 92,4 % | 89,6 % (peor: pierde las tardías) |
| **lote del texto/ref2 si existe; si no, la más reciente** (lo que hace v3) | 100 % | **96,1 %** (PENA 100 %, resto 93,6–96,0 %) |
| + exigir ≥1 día entre cobro y devolución | 100 % | 96,4 % |
| + preferir cobros a 2–14 d, luego la más reciente | 100 % | 96,3 % |
| + libro mayor oráculo (excluir cobros ya devueltos) | 100 % | 96,1 % (sin ganancia) |
| asignación voraz devolución↔cobro sin repetir | 100 % | 95,5–96,3 % (sin ganancia) |

**Techo de una regla general ≈ 96,4 %.** El error restante (~3,6 %) está íntegramente en devoluciones tardías (>14 d,
el 9,9 % del total, 65,5 % de acierto): mismo mandato e importe cobrado varias veces (cuotas fijas mensuales), y la
más reciente no es la devuelta. Entre las ≤14 d el acierto es 99,4–99,6 %. Ningún truco de libro mayor lo arregla; haría
falta otra señal (p. ej. patrón de fechas del banco) y no la he encontrado.

---

## 2. Duplicados (`ar_duplicate_payment`, `ap_duplicate_payment`)

### Qué son y qué comparten con el pago original

Para cada línea duplicada (3.780 AR / 411 AP con original localizable: mismo `item_id`, anterior, sin exception):

| | AR | AP |
|---|---|---|
| Desfase duplicado − original (días) | min 1 · p50 7 · p90 12 · p99 14 · máx 15 | min 0 · p10 14 · p50 28 · p90 49 · máx 50 |
| Importe igual al original | 100 % | 100 % |
| Misma cuenta bancaria | 93,9 % (3.550) | 100 % |
| Mismo banco | 98,3 % | 100 % |
| Texto idéntico al original | 56,6 % | 75,7 % |
| Original sin referencia a la factura (solo nombre+importe) | 63,9 % (2.393 `ref_noise`) | 18,7 % |
| **El duplicado cita el nº de factura** | **35,9 %** (1.355) | **81,3 %** (334) |

- Si el duplicado cita la factura, el original también la citaba (solo 3 casos distintos): la referencia no ayuda a
  distinguirlo, solo a decidir que ya estaba liquidada (la única discrepancia: 3 originales citan y su duplicado no).
- **El 64 % de los duplicados AR no cita la factura** (hereda el original sin referencia: solo pagador + importe).
  En AP son el 19 %.
- Reintentos: el duplicado AR llega de 1 a 15 días después, el AP de 0 a 50. Fuera de esas ventanas no hay duplicados.
- Antigüedad de la factura en el momento del duplicado: AR p50 49 d, p99 234 d (1,5 % > 200 d); AP p50 84 d,
  p99 166 d (0,2 % > 200 d). Relevante para el corte de 200 d de `load_items` en LIVE.

### Cobertura de reglas candidatas

Dos mediciones. (a) **Solo líneas de banco** (importe/cuenta/ventana/texto frente a líneas previas, sin libro mayor):
precisión baja porque hay muchísimas líneas con importes repetidos (cuotas fijas, runs de pagos):

| Regla (sobre cualquier línea previa) | Marcadas | TP | Precisión | Recall |
|---|---|---|---|---|
| AR: mismo importe+cuenta, 1–15 d | 29.037 | 3.551 | 12,2 % | 93,6 % |
| AR: + mismo texto (sin dígitos), 1–15 d | 4.816 | 2.180 | 45,3 % | 57,4 % |
| AP: mismo importe+cuenta, 8–50 d | 18.800 | 418 | 2,2 % | 98,4 % |
| AP: + mismo texto, 8–50 d | 14.124 | 377 | 2,7 % | 88,7 % |

(b) **Contra partidas ya liquidadas** (el original debe ser un pago que liquidó una partida; importe igual al de
una partida liquidada hace 1–15 d AR / 8–50 d AP; proxy del libro mayor usando golden solo para la tabla):

| Regla | Marcadas | TP | Precisión | Recall |
|---|---|---|---|---|
| AR: importe = partida liquidada, 1–15 d | 59.898 | 3.780 | 6,3 % | 99,6 % |
| AR: + misma cuenta | 27.102 | 3.553 | 13,1 % | 93,6 % |
| AR: + mismo texto | 4.677 | 2.282 | 48,8 % | 60,1 % |
| AR: + misma cuenta + mismo texto | 4.398 | 2.180 | 49,6 % | 57,4 % |
| AP: importe = partida liquidada, 8–50 d | 19.788 | 410 | 2,1 % | 96,5 % |
| AP: + misma cuenta | 2.908 | 410 | 14,1 % | 96,5 % |
| AP: + mismo texto | 790 | 369 | 46,7 % | 86,8 % |
| AP: + misma cuenta + mismo texto | 613 | 369 | 60,2 % | 86,8 % |

**Conclusión.** Ni importe ni importe+texto bastan como regla aislada (precisión ≤ 60 %). Lo que distingue un duplicado
de un pago normal es que **el pagador identificado (referencia o IBAN/nombre) apunta a una partida ya liquidada y no
queda ninguna abierta de ese importe**: el matcher actual ya prueba primero las abiertas y solo cae a las liquidadas
cuando no hay encaje. Con el pagador conocido el recall sube a ≈100 % (importe igual en el 100 %) y la precisión depende
de la identificación del pagador, que no puedo medir sin ejecutar el matcher. El texto idéntico por sí solo es un
refuerzo (precisión ~50–60 %, recall 57–87 %), útil como último recurso sin pagador identificado.

Orden de reglas por cobertura (AR, de 3.780): 1) referencia citada → partida liquidada (35,9 %); 2) pagador + importe
exacto → partida liquidada ≤ 15 d (resto 64 %, limitado por identificación de pagador de `ar_ref_noise`);
3) último recurso: importe+texto idénticos a una línea liquidadora reciente (+~0–10 %, precisión ~50 %).
AP (de 411): 1) referencia (81,3 %); 2) pagador + importe ≤ 50 d (resto 19 %); 3) importe+texto idénticos.

---

## 3. Reembolsos de proveedor de abonos (`ap_supplier_refund`)

Línea **positiva** de un proveedor, importe **exactamente** igual al `total_amount` de un abono APC abierto del mismo
proveedor (15.280/15.280 líneas, un solo ítem APC por línea, `reason = "Abono de proveedor"`, todos con
`related_document`). No es una categoría: liquida el APC.

| Señal | Líneas | % |
|---|---|---|
| Cita el nº del abono (`ABONO AB-FAC2300049`, `RMR*IV*…`) | 6.054 | 39,6 % |
| Cita la factura original (`related_document`) pero no el abono | 3.004 | 19,7 % |
| Solo palabra clave (`ABONO`, `REEMBOLSO`, `DEVOLUCION SALDO A FAVOR`, `REFUND`, …) | 2.726 | 17,8 % |
| Nada: solo nombre del proveedor + importe | 3.496 | 22,9 % |

- Desfase emisión del abono → reembolso: min 7 d, p10 13, **p50 34**, p90 56, máx 63 d.
- **Proveedor + importe exacto + abono APC emitido en los 70 d anteriores identifica un único ítem en el 100 %** de
  los casos. Con solo proveedor y ventana de 70 d hay 1 candidato en 66 %, 2 en 23 %, ≥3 en 11 %.
- De 45.531 abonos AP pre-2025, 15.280 (34 %) se reembolsan; el resto se compensa en el siguiente pago (y no tiene
  línea propia): la señal distintiva es “entra dinero de un proveedor”.
- Se confunde solo con el **reembolso de nuestro pago duplicado** (categoría `SUPPLIER_REFUND`, sin ítem; 420 líneas):
  también entra dinero de un proveedor, pero el importe coincide con un duplicado AP (−importe) de 14–50 d antes
  (p50 32 d; 416/420 con una sola coincidencia por importe en 120 d) y **solo el 50 % lleva una palabra clave**
  (`DUPLICADO`, `PAGO DOBLE`, `DOUBLE`…). Igual para `CUSTOMER_REFUND` (2.257): 45 % con palabra clave, 14–47 d tras el
  duplicado AR, 2.206 con coincidencia única por importe. → Los reembolsos de duplicados necesitan un registro de
  duplicados detectados (ver §5), no basta el texto.
- Regla propuesta: entrada de proveedor identificado → (1) APC por nº citado / factura relacionada; (2) APC abierto
  del proveedor con importe exacto; (3) si no hay APC y el importe = un duplicado AP reciente → `SUPPLIER_REFUND`.

---

## 4. Qué cubre ya `model/v3` (solo lectura de `bankrec.py`)

No he ejecutado `bankrec.py` (la fase `dev` usa 2025-04/05, prohibido). Las cifras de dev son las ya registradas en
`SUBMISSIONS.md`; las emuladas son mi reproducción en SQL de la regla sobre datos < 2025.

| Segmento | Regla v3 (`bankrec.py`) | Cobertura | Fuente |
|---|---|---|---|
| `ar_dd_returned` | `pass_returns` L845–864: mandato + importe + `collection_date ≤` devolución, ordenadas por (lote en texto, fecha) desc; ítem por `settled_by_row` o `dd_item`; si no, ítem del cliente del mandato con total igual (más reciente) | **96,1 %** emulado (PENA 100 %, resto 93,6–96,0 %); dev registrado 96 % | emulación + SUBMISSIONS |
| | Residuo | ≈3,6 % tardías ambiguas (ver §1); 132 sin original en TRAIN | |
| `ar_duplicate_payment` | `dup_check` L1134: solo ítems `open==0`, importe=total, antigüedad ≤400 d (refs) / ≤200 d (pagador); se activa si hay referencia citada sin abiertas (L1112), o en `pass_party` L1423 cuando el pagador identificado no encaja con abiertas | dev registrado **80 %** | SUBMISSIONS |
| `ap_duplicate_payment` | mismo `dup_check` por referencia (81 % citan factura) o pagador | sin cifra registrada | – |
| `ap_supplier_refund` | `pass_party` L1393–1404 (entrada de proveedor con score ≥0,75 → APC abierto por importe) y `apc_by_related` L1126 (factura citada) | dev registrado **98 %** | SUBMISSIONS |
| `ap_duplicate_refund` / `customer_refund` | `pass_keywords` L933–940 por palabras clave | `customer_refund` 97 % (dev); solo cubre el ~50 % con palabra clave por texto; no usa importe frente a duplicados detectados | SUBMISSIONS + §3 |

Huecos detectados leyendo el código:

1. **Índice de liquidadas en LIVE/FINAL** (`load_items` L196–211): solo conserva ítems con `issue_date ≥ start−200 d`
   (o en `open_items_at_start`) y les pone `open=0`. Se pierden liquidadas antiguas (1,5 % de los duplicados AR por
   antigüedad >200 d) y no se sabe *cuándo* se liquidó cada una. En dev (`load_items_dev` L250–306) el criterio es otro:
   “liquidada por golden en los 120 d previos”. **Dev y LIVE no calculan lo mismo.**
2. En FINAL, las partidas liquidadas en LIVE solo se conocen por las asignaciones de LIVE; `load_items` no las lee.
3. `dup_check` exige importe == total del ítem: un duplicado de un pago PARTIAL o con descuento no se detecta.
4. `assign` (L414–421) no deja rastro de duplicados detectados: no hay registro para casar luego el reembolso
   (`SUPPLIER_REFUND` / `CUSTOMER_REFUND`) por importe.
5. Una devolución cuyo ítem no está en `items` (liquidado hace >200 d) cae sin asignar; las devoluciones tardías (75 d)
   dependen del mismo corte.

---

## 5. Diseño (sin implementar): índice de partidas liquidadas

### Definición única, igual en dev, LIVE y FINAL

```
known(start)     = ítems de todos los ficheros de facturas/abonos con issue_date < start
                   (TRAIN; + LIVE si phase = final; dev: TRAIN anterior a la ventana)
open0(start)     = open_items_at_start         (dev: reconstruido de golden de TRAIN antes de la ventana,
                                                   ya existe en load_items_dev)
settled0(start)  = known(start) − open0(start)           # liquidadas antes de la fase
settled(t)       = settled0 ∪ {ítems cuyo open pasa a 0 durante la fase hasta t} − {reabiertos por devolución}
```

Entrada del índice por ítem liquidado: `item_id, tipo, parte, moneda, total, last_paid_date, last_paid_txn, last_paid_amount,
last_paid_account, text_mask, n_pagos`, más `source` ∈ {golden, open_items_diff, matcher}.

- **`settled0`**: todo lo conocido que no figura abierto. No hay fecha de liquidación en `open_items_at_start`, así que
  `last_paid_date` se toma de golden de TRAIN cuando existe (cubre hasta 2025-06-30 para LIVE) y, si no existe
  (FINAL sobre liquidaciones de LIVE; ítems de antes de TRAIN), se rellena con la fecha de la asignación propia o `NULL`
  (= “antes del inicio”). Las ventanas de duplicado (AR ≤ 15 d, AP ≤ 50 d, margen a 60 d) hacen que `NULL` solo importe
  en los primeros 60 d de la fase; ahí se acepta “liquidada antes del inicio” sin comprobar fecha.
- **Sin corte por antigüedad de emisión**: el criterio de poda es por *fecha de liquidación* (≥ start − 60 d), no por
  `issue_date`; así la edad de factura (p99 234 d en AR) deja de importar y el índice es pequeño: ~(liquidaciones de los
  últimos 60 d), no los ~4,5 M de ítems conocidos que no están abiertos (en LIVE hay 325 k abiertos).
- **Mismo cálculo en las tres fases**: una sola función `settled_index(phase)` que parte de `known − open0`; el único
  insumo que cambia es `open0` (golden-derivado en dev, fichero en LIVE/FINAL). Comprobación de equivalencia a implementar
  primero: reconstruir `open0` por golden para un corte *anterior a 2025* y verificar que el índice resultante produce
  el mismo `settled0` que el método diff (no puedo hacerlo con `open_items_at_start` real sin tocar 2025).

### Qué consume

| Consumidor | Consulta | Ventana |
|---|---|---|
| Duplicado AR/AP | partida del pagador (por ref o IBAN/nombre) con `open==0` e importe = pago, y `last_paid_date` en [l−15, l−1] (AR) / [l−50, l] (AP) | sin límite de `issue_date` |
| Duplicado sin pagador (último recurso) | `last_paid_amount == importe` y misma `text_mask` y misma cuenta | 60 d |
| Devolución DD | fila del lote por mandato+importe (§1); si el ítem de esa fila está en `settled`, se reabre; si no está (no estaba conocido) se crea desde la fila del lote | 75 d |
| Reembolso de duplicado | registro `refundable_dups` (abajo) | 50 d |

### Reposición del saldo al devolver

Al asignar `RETURNED` a una devolución enlazada con la asignación original `(txn_o, item, importe_o)`:

1. `open += importe_o` (el importe re-abierto es el de la asignación original: coincide 100 % con el importe de la línea,
   incluido PARTIAL si el original fue parcial; `assign` ya hace `it.open += amt` salvo que el ítem no esté en `items`).
2. Sacar el ítem de `settled` (o decrementar `paid`); anotar `returned_on` y la devolución en el historial.
3. Marcar el cobro original como “devuelto” para que una segunda devolución (696 ítems devueltos más de una vez)
   no enlace el mismo cobro dos veces (en la práctica no aporta precisión: 96,1 → 96,1 %, pero evita reabrir de más).
4. Si luego llega la re-presentación (`re_presentation=Y`, o transferencia posterior), el ítem vuelve a estar abierto y
   se liquida por la vía normal (golden: sin exception) y vuelve a entrar en `settled`.

### Registro de duplicados detectados

Cuando se marca `DUPLICATE_PAYMENT`: guardar `refundable_dups[(cuenta, signo, importe)] += (txn, parte, fecha)`.
Un reembolso posterior (entrada de proveedor o salida a cliente) de importe igual, de signo contrario y a ≤ 50 d,
con parte coincidente o sin parte identificada, se clasifica `SUPPLIER_REFUND` / `CUSTOMER_REFUND` (categoría, sin ítem).
Medido: coincidencia única por importe en 416/420 (AP) y 2.206/2.257 (AR→cliente) frente a duplicados reales, 14–50 d.
No marca duplicados de FINAL contra LIVE a menos que el índice incluya las asignaciones de LIVE (hueco 2).

### Riesgos / pruebas

- Verificar que `settled0` no incluye ítems que sí están en `open_items_at_start` con `open_amount < total` (PARTIAL):
  30.827 ARI abiertos son parciales en LIVE; un parcial no es “liquidado”, un segundo pago es un pago normal.
- Un pago de importe = total sobre un ítem con pagos parciales previos no es duplicado.
- Evaluar en dev que desplazar el criterio de “120 d por golden” a “últimos 60 d por fecha de liquidación” no mueve el
  score de `ar_duplicate_payment` (80 %).

---

## 6. Orden de reglas por cobertura (volumen de líneas × acierto esperado)

| # | Regla | Líneas pre-2025 | Cobertura medida | Líneas ganables sobre v3 |
|---|---|---|---|---|
| 1 | Devolución DD: mandato+importe, lote del texto si existe, si no la cobrada más reciente | 22.885 | 96,1 % emulado | ≈ 0–0,3 pt (v3 ya 96 %); techo 96,4 % |
| 2 | Reembolso de APC: entrada de proveedor identificado + APC abierto por importe exacto (+ nº citado / factura relacionada) | 15.280 | 100 % de unicidad con proveedor+importe+70 d; v3 98 % | ≈ 2 % |
| 3 | Duplicado AR: partida liquidada citada o por pagador+importe ≤ 15 d, sin corte de `issue_date` | 3.795 | 35,9 % por referencia; resto depende de pagador; v3 80 % | hasta ≈ 20 % × 3,8 k |
| 4 | Reembolso de duplicado a cliente (`CUSTOMER_REFUND`) por importe frente a duplicados detectados | 2.257 | 97,7 % de unicidad por importe; keyword solo 45 % | pequeño (v3 97 % en dev) |
| 5 | Duplicado AP: ref (81 %) o pagador+importe ≤ 50 d | 425 | 81,3 % por referencia | hasta ≈ 19 % × 425 |
| 6 | Reembolso de duplicado de proveedor (`SUPPLIER_REFUND`) por importe frente a duplicados AP | 420 | 99 % de unicidad; keyword solo 50 % | hasta ≈ 50 % × 420 |

Impacto en puntuación (por ROADMAP: una clase de excepción pasa de 0 a 0,8 F1 = ~1,6 pt): el mayor margen está en
**la clase `DUPLICATE_PAYMENT`** (F1 conjunto AR+AP, sensible a AP porque pesa igual por clase), no en devoluciones, que ya
están cerca del techo.

## 7. Abierto

- Medir v3 sobre una ventana pre-2025 (p. ej. `--months 202410 202411`) en lugar de 2025-04/05 para tener cobertura real
  por segmento sin contaminar dev. No lo he lanzado: toca `bankrec.py`/cachés y no estaba pedido.
- Precisión real de las reglas de duplicado una vez identificado el pagador (depende de `identify_party`).
- Buscar señal adicional para las devoluciones tardías ambiguas (3,6 %).
