# Auditoría de robustez para la FINAL (solo lectura)

Rama `model/v3-ledger` (v3.5 fusionada, commit `9d2adfd`). No se ha tocado `bankrec.py` ni se ha escrito código de
pipeline: solo lectura de `solution/bankrec.py`, `src/**`, `solution/passes_ledger.py`, `run_v35.sh`, y análisis de
`~/Downloads/sub_live_v35.csv.gz` + `live/` + `masters/` (estado a 2025-12-31).

**Límites del análisis (leer antes de fiarse de los números)**
- No existe `final/` en la máquina (ni `masters_final/`): lo que se dice de FINAL es inferencia del código y de POLICIES §5-§6.
- La salida de v3.5 tiene solo 5 columnas (`bank_txn_id,item_id,amount,category,exception`). **No hay campo de
  explicación/pase.** `src/pipeline.py` admite `--sources` (bank_txn_id → pase) pero `run_v35.sh` no lo usa. "Sin avisos
  enlazados" lo he tenido que **aproximar** (ver §3).
- "UNIDENTIFIED" = cobro (importe > 0) con `exception=UNIDENTIFIED`. En pagos no se puede medir: `pipeline.fallback()`
  mete todo lo no resuelto como `BANK_FEES` (pagos) o `UNIDENTIFIED` (cobros).
- Los volúmenes de "movimientos afectados" son órdenes de magnitud sobre LIVE (203.083 líneas, 124.428 cobros), no mediciones de pérdida.
- Nota de reproducibilidad: el working tree **no** coincide con `9d2adfd` (`bankrec.py` importa `passes_ledger` y llama a
  `pass_dd_returns`; `passes_ledger.py` sin commitear). `run_v35.sh` paso 1 ejecuta `solution/bankrec.py` del working tree.

---

## 1. Supuestos fijos en el código

| # | Supuesto | Dónde | Con ZA01 (ZAR, "KWI…", renombrada 2026-04-01) | Con maestros nuevos |
|---|---|---|---|---|
| 1 | Entidades de retención `{"MX01","BR01","MA01"}` | `bankrec.py:101`; `ar_match.py:15` `WH_COUNTRIES={"MX","BR","MA"}`; `ar_match.py:710` usa `t.entity[:2]` como país | Sin retención en ZA01: coherente con POLICIES §3. El truco `entity[:2]` solo vale porque el prefijo coincide con el país (en UK01 daría "UK", no "GB"; no afecta aquí) | Si la FINAL añade retención en otro país, no se detecta |
| 2 | Subsidiarias de fábrica `FACTORY_SUBS={"ES00":{ES00,ES01,ES02,PT01}}` | `src/passes/batches.py:27`, usado en `ap_match.py:78` | ZA01 queda fuera: bien, nadie paga por ella | — |
| 3 | País SEPA (`SEPA_COUNTRIES` en `bankrec.py:100`, `SEPA_EUR` en `ar_match.py:13`) para "comisión bancaria transfronteriza" | `bankrec._non_sepa` (l.1377), `ar_match.cross_border` (l.730) | `ZA` no está en ninguna lista. En `cross_border`, si el país del cliente (`ctry`) ≠ país de la entidad y no es SEPA → candidato a `SHORT_BANK_CHARGES`. El país de la entidad sale de `masters/entities.csv`; **si `ZA01` falta ahí, `ec=""`** y todo cliente ZA con faltante pequeño se etiqueta como comisión | Depende de que `entities.csv` de `masters_final` traiga ZA01 |
| 4 | Divisas: no hay lista cerrada de divisas en el pipeline (solo `docs_extract.py:35` `CURRENCIES` para parsear avisos, **incluye ZAR**) | — | ZAR OK en parseo; tipos de cambio ZAR existen en `masters/fx_rates.csv` desde 2021 | ver fila 5 |
| 5 | Tipos de cambio: v3 mira solo **10 días atrás** (`bankrec.py:398-406`) y devuelve `None`; v3.5 usa el último disponible sin límite (`ar_index.py:94-101`) | `fx_rates.csv` de `masters/` acaba en **2025-12-31** | Con maestros viejos: v3 sin FX para todo 2026 (silencioso); v3.5 usa tipo estancado (>3 % de tolerancia en divisas volátiles ⇒ no casa) | Con `masters_final` (hasta 2026-06-30) todo bien |
| 6 | Rutas de maestros: prefiere `final/masters_final/` solo si `phase=="final"` **y el directorio existe**; si no, **cae en `masters/` sin avisar** | `bankrec.py:346-349`; `src/data.py:115-124` (`load_masters`) | Sin `masters_final`: no hay clientes `CZA01…`, ni entidad, ni cuenta ZA01 ⇒ todo ZA01 sin payer | Con `masters_final` **parcial** (faltan ficheros): `bankrec` lee 4 ficheros fijos (`bank_accounts, payment_terms, customers, suppliers`, + `fx_rates`) ⇒ `FileNotFoundError` y se cae la corrida; `data.load_masters` solo lee lo que haya ⇒ `KeyError` posterior en `ar_index` (`m["customers"]`, `m["entities"]`, `m["payment_terms"]`, `m["fx_rates"]`) |
| 7 | Prefijos de numeración de facturas | No hay lista. Claves por dígitos (`bankrec.py:331-338`: `dg`, `dg[2:]`, `dg[2:].lstrip("0")`, últimos 6/7; exige ≥5 dígitos) y `doc_number` normalizado; `ar_index` indexa `(entity, nd)` y `#dígitos` (≥6) | "KWI…" sirve por clave normalizada; `dg[2:]` asume 2 dígitos de año al principio (inocuo si no los hay). Facturas ZA01 con <5 dígitos solo casan por `nd` exacto. Índices por entidad ⇒ sin colisión con otras | — |
| 8 | Proformas: `PROFORMA|PRO-FORMA|\bPF\d{2}-\d{4,}` | `bankrec.py:934`; `nonitem_rules.py:16` (`\bPF\d\d-\d`) | `PF26-…` entra (dos dígitos de año). Una proforma con otro prefijo en ZA01 no se detectaría salvo que diga PROFORMA | — |
| 9 | Mandatos DD `VTM-[A-Z]{2}-\d{7}` / `VTM-[A-Z]{2}-\d+` | `bankrec.py:848`, `passes_ledger.py:11`, `batches.py:21,401`; devoluciones `RETURN_RX` en `nonitem.py:74` incluye `VTM-` | ZA01 no usa SEPA; sin impacto | Si cambian el patrón de mandato en FINAL, devoluciones DD (≈3.300/semestre) caen a fallback |
| 10 | Calendario: `PHASE_RANGE` (`202601`–`202606` para final), `DEV_MONTHS`, `month_window(before=3, after=1)` para ficheros auxiliares (batches, runs, tarjeta, cheques) | `bankrec.py:516-526,238` | Ficheros `ZA01_*` entran si siguen el patrón `_YYYYMM` (regex `_(\d{6})` sobre el nombre) | Corte duro: ficheros auxiliares fuera de la ventana se ignoran; confirming creado >3 meses antes del cargo no se ve |
| 11 | Calendario de aperturas: `open_items_at_start` + `start={"live":"2025-07-01","final":"2026-01-01"}`; partidas liquidadas solo de los últimos 200 días (v3) / 730 días (v3.5 `data.py:159`) | `bankrec.py:195-206`; `data.py:140-162` | Items ZA01 solo existen desde 2026-01-01 (no hay ARI de ZA01 en LIVE: 0 filas en ar/ap/cn/open) | — |
| 12 | Diseños de extracto por banco | **No hay parsers por banco**: se usa `bank_transactions.csv.gz` ya normalizado. `statement_format` no se lee en ningún sitio. Hay claves por `(bank, bank_code, bank_subcode, firma_texto)` en el modelo de categorías de v3 (`bankrec.py:1630-1632`) con degradación a `(bank_code, firma)` | Banco `UMVC` (MT940, "EFT CREDIT …") no existe en TRAIN ⇒ el modelo de categorías cae a `(bank_code,firma)`; reglas de texto en ES/EN/FR/DE/PT/IT (`nonitem_rules.py`) cubren inglés genérico ("SERVICE FEE", "COMMISSION", "INTEREST SETTLEMENT") pero no jerga sudafricana (p. ej. "EFT", "STAMP", "NOTIONAL INT") | Moncayo ya migró a camt.053: sin deterioro visible (§3) |
| 13 | Diseños de aviso | Parsers por **formato de fichero** (`docs_extract.py`: pdf/csv/xlsx/txt), sin plantillas por cliente. Un formato nuevo de extensión (xml/json) se ignora en silencio | ZA01 no tiene `remittance_advices/ZA01` en LIVE | Cambios de cabeceras en xlsx: ver §3 |
| 14 | Caché de parquet/pickle por **fase, no por contenido**: `data.py:_cached("items_{ph}")`, `("bank_{ph}")`, y `solution/cache/open_start_{phase}_v2.pkl` en `bankrec.py:200` | `data.py:57-65`, `bankrec.py:200-216` | Una corrida previa de `final` con datos parciales deja caché que la corrida buena reutiliza | Igual con maestros actualizados: el pickle de `open_start_final` incluye items, no maestros, pero `items_final` sí congela ficheros |
| 15 | Cuentas: `acc_ent` desde `bank_accounts.csv` con fallback `t.entity` o `account_id[:4]` | `batches.py:69-72,141`, `ap_match.py:29,77` | `ZA01-UMVC-ZAR` no está en `masters/bank_accounts.csv` y **la tabla ya está incompleta hoy**: `IT01-VALD-EUR-2` (10.529 líneas en TRAIN y 1.689 en LIVE) tampoco está. Los fallbacks lo cubren. Frágil si un `account_id` no empieza por la entidad | — |
| 16 | `pipeline.fallback`: cobro no resuelto → `UNIDENTIFIED`; pago no resuelto → `BANK_FEES` | `src/pipeline.py:24-30` | Toda línea de ZA01 sin pase que la cubra termina así (salvo las dos proformas) | Cualquier banco/idioma nuevo: los pagos acaban en `BANK_FEES` (en LIVE: 10.072 líneas `BANK_FEES`, 13 % de los pagos; el 99,5 % con importe <1.000; 50 líneas >1.000, 30 de ellas en diciembre) |

---

## 2. Los 2 movimientos de ZA01 en LIVE

Datos (`live/bank_transactions.csv.gz`), cuenta `ZA01-UMVC-ZAR`, MT940 (`.sta`), banco `UMVC`, fichero `…_202512.sta`:

| bank_txn_id | fecha | importe | texto | salida v3.5 |
|---|---|---|---|---|
| BT-2025-0400852 | 2025-12-29 | +58.327,43 ZAR | `EFT CREDIT POOL & SPA TAYLOR LTD PROFORMA PF25-0100001` | `category=CUSTOMER_ADVANCE`, sin partida, sin excepción |
| BT-2025-0401478 | 2025-12-30 | +62.069,06 ZAR | `/ORDP/OUTDOOR LIVING ASHBRIDGE LTD/REMI/PROFORMA PF25-0100002` | `category=CUSTOMER_ADVANCE`, sin partida, sin excepción |

Cómo llega ahí (leído del código, no ejecutado): en `bankrec.py:934` un cobro con `PROFORMA` o `PF\d{2}-\d{4,}` va a
`CUSTOMER_ADVANCE`; `nonitem_rules.py:16` tiene la misma regla. "EFT CREDIT POOL" no dispara `CASH_POOL` (`CASH ?POOL`).
Hay dos PDFs de proforma en `live/pdfs/ZA01/` (`PF25-0100001/2`, party `CZA01000008` / `CZA01000022`) y **ningún
cliente/cuenta/entidad ZA01 en `masters/`**. No hay golden para validar el acierto; por POLICIES §4 (proforma ⇒ anticipo)
es lo esperable.

Qué implica para FINAL: estas dos son el único caso en que el pipeline acierta por un patrón textual que no depende de
maestros. Todo cobro ZA01 que sea una factura "KWI…" necesita: `customers` de `masters_final` (nombre/IBAN),
items KWI en `final/ar_invoices`, y entidad en `entities.csv`. Los anticipos futuros (`PF26-…`) se enlazarán con la
factura vía la cláusula "advance received against proforma" del PDF solo si el PDF sale en `final/pdfs`.

---

## 3. Deriva observable en LIVE (sin golden)

Método: salida v3.5 × `live/bank_transactions`. Cobros = importe > 0.

**Global por mes (cobros)**: % UNIDENTIFIED = 6,7 % (jul) · 7,6 · 8,1 · 7,7 · 7,3 · **8,5 % (dic)**; sin partida (incluye
categorías no-UNID) 7,9 · 9,0 · 9,6 · 9,1 · 8,8 · 9,9 %. Deriva suave al alza: +1,8 pt UNID en seis meses.

**Por entidad (% UNID de cobros, jul→dic)**: AU01 5→8 · BR01 9→10 · CH01 7→6 · CN01 5→6 · DE01 9→8 · ES01 7→8 · ES02 8→8 ·
FR01 6→9 · IT01 6→7 · MA01 6→9 (11 en oct) · MX01 6→10 · NL01 7→8 · PL01 6→7 · PT01 6→**12** · UK01 8→**14** · US01 7→9;
ES00 ≈0 (no recibe cobros de clientes). Tabla completa (n y %) reproducible con el script del scratchpad; las que más se
mueven en dic son UK01, PT01, MX01, PL01, y las cuatro tienen pocos cobros (400-650/mes), así que el ruido es ±1,5 pt.

**Quiebre de Moncayo (camt.053 desde 2025-10-01)** — no hay quiebre; se ve mejora:

| Mes | Formato | Cobros | % UNID | % de líneas (cobros+pagos) con partida |
|---|---|---|---|---|
| 2025-07 | n43 | 1.334 | 9,1 | 79,7 |
| 2025-08 | n43 | 1.138 | 9,7 | 83,5 |
| 2025-09 | n43 | 1.282 | 8,2 | 82,9 |
| 2025-10 | camt053 | 1.208 | **6,5** | 81,8 |
| 2025-11 | camt053 | 988 | 7,3 | 85,7 |
| 2025-12 | camt053 | 1.308 | 7,6 | 84,4 |

Pagos `BANK_FEES` por Moncayo: 26,0 / 26,1 / 23,2 % (n43) → 23,5 / 25,7 / 23,9 % (camt): sin cambio. La razón probable
de la mejora: camt trae `counterparty_name`, `counterparty_account` y `end_to_end_id` estructurados.
Lo que **no** he podido comprobar: aciertos reales (sin golden); solo que la proporción de "sin resolver" no sube.

**Cliente grande desde septiembre de 2025** — no es un solo cliente: en `remittance_advices/index.csv` los avisos `xlsx`
pasan de 48-50/mes (jul-ago) a 95-110/mes (sep-dic) y los pagadores con xlsx de 21-24 a 36-42 (Aquatics Norte,
Schwimmbadbau Navarro, North Arden, Méndez Aquatique, TMR Rega… ya con xlsx desde sep). Para cobros que casan por
nombre con un pagador con avisos y tienen aviso (±10/+30 días):

| Formato del aviso | % UNID antes (jul-ago) | % UNID después (sep-nov) |
|---|---|---|
| csv | 5,7 | 6,6 |
| pdf | 5,4 | 6,5 |
| txt | 6,0 | 5,9 |
| **xlsx** | 6,7 | **5,9** |
| sin aviso | 7,2 | 8,1 |

El xlsx nuevo no deteriora; el aumento de ~1 pt es general (csv/pdf también suben) y coincide con la deriva global.

**Filas sin avisos enlazados** (aproximación): cobros multi-partida (≥2 partidas en salida) cuyo pagador casa por nombre
con un pagador de `index.csv` (misma entidad) y **sin aviso en [−10, +30] días**: 12,2 % (jul), 12,2, 12,2, 11,4, 10,1,
**17,6 % (dic)**. El pico de dic es **censura por la derecha** (avisos posteriores al 31-dic no están en LIVE: UNID de cobros
sin aviso 11,8 % en dic vs 6-8 % antes); se repetirá en junio de FINAL (≈1/6 del semestre). Por entidad (multi-partida sin
aviso, sep→nov): IT01, PT01, UK01, FR01, NL01, MX01, PL01 entre 18 y 34 %; US01 y DE01 por debajo del 13 %.
Limitación: el casamiento por nombre usa las 10 primeras letras normalizadas; subestima en entidades con nombres
truncados.

---

## 4. IT01: cambio de cuenta de cobros (2026-02-01)

**Cómo identifica el pipeline al cliente** (no a *nuestra* cuenta):
1. IBAN pagador contra `customers.iban` (un único IBAN por cliente): `bankrec.py:468` (`party_by_iban`) y `ar_index.py:125`.
   Solo v3.5 añade los IBAN de **avisos de cambio de cuenta en PDF** (nuevo y antiguo, `pdfinfo.new_ibans`,
   `ar_match.py:62-65`); v3 no.
2. Nombre (fichas del maestro + nombres impresos en facturas de los últimos ~200/400 días), referencia, importe.
3. Mandato DD para devoluciones.

**Cómo identifica nuestra cuenta**: `entity` y `account_id` vienen de la propia fila bancaria; el casamiento AR se hace por
**entidad** (`cust_items`, `name_idx[ent]`), no por cuenta. Un pago a la cuenta nueva de IT01 casa igual que a la vieja
siempre que la fila traiga `entity=IT01`.

**Dónde sí importa la cuenta (y se rompe si cruza cuentas)**:
- Lotes DD, tarjetas, cheques: búsqueda por importe `dd_by_amt[(account_id, total)]`, `card_by_amt`, `dep_by_amt`
  (`batches.py:81,91,104,379,462,544`; `bankrec.py:563-579`). En LIVE los 3.709 recibos DD de IT01 declaran
  `IT01-VALD-EUR`. Si en 2026 el lote declara una cuenta y el cargo cae en otra, el casamiento por id de lote funciona
  (`_same_acc` cae a entidad, `batches.py:141`) pero el casamiento por importe/fecha **falla**.
- `paid_lines[(cuenta,−importe)]` (rechazos de transferencia, 90 días), reembolso de duplicado al cliente
  (`nonitem._customer_refunds`: cobro previo **en la misma cuenta** ≤90 días), pares de anulación bancaria (±3/±10 días,
  misma cuenta): si el cobro entra en la cuenta nueva y el reembolso/anulación sale de la vieja (o viceversa) no se emparejan.
- Los maestros ya van por detrás de la realidad: `IT01-VALD-EUR-2` existe en el banco y no en `bank_accounts.csv`.
  `IT01-RHEI-EUR` (9 líneas en LIVE, solo cash pool/comisiones) figura como "Operativa / participante cash pool";
  nada en el código filtra por `purpose`, así que un cambio de uso no rompe.

Volumen expuesto: IT01 = 8.048 líneas LIVE (4.880 cobros). El efecto directo es pequeño (UNID IT01 6→7 %); lo expuesto son
las rutas por cuenta (devoluciones DD de IT01, reembolsos, rechazos).

---

## 5. Pagos más lentos en 2026: ventanas fijas

Lo que depende de **cuándo paga el cliente** (riesgo real) frente a lo que depende de **fichero↔banco** (riesgo bajo, no
cambia con la morosidad):

| Ventana | Dónde | Efecto con retrasos mayores | Tipo |
|---|---|---|---|
| Edad máx. de la partida para `DUPLICATE_PAYMENT`: 400 días desde emisión (`dup_check`), 200 en `pass_party` | `bankrec.py:1136,1425` | Factura cobrada tarde + duplicado aún más tarde ⇒ fuera de ventana ⇒ duplicado no detectado (cae a UNID o se cuela como pago nuevo) | cliente |
| Partidas ya liquidadas recientes: 200 días antes de la fase (v3) / 730 (v3.5) | `bankrec.py:205`; `data.py:159` | Idem; en v3 las liquidadas hace >200 días dejan de verse como duplicado | cliente |
| Reembolso de duplicado a cliente: cobro previo en la misma cuenta ≤90 días | `nonitem.py:111` | Reembolso más tardío ⇒ queda como pago genérico | cliente |
| Devolución de transferencia de proveedor ≤90 días | `batches.py:271` | Retorno tardío no se empareja | cliente/banco |
| Subset-sum de partidas: compras emitidas ≥400 días antes y vencimiento ≤ fecha+20 días; tope `2 ≤ len(P) ≤ 40`; `neg[:4]`, `pos[:30]`, `DP_MAX_POOL=80`, `MAX_NODES=6000`, `len(sel)>80` | `ar_match.py:511-512,616-617,570-571,762,13-18`; `bankrec.py:~1330-1370,806` | **Más partidas vencidas abiertas por cliente** ⇒ conjuntos candidatos más grandes y más ambiguos ⇒ se superan topes ⇒ búsqueda abortada ⇒ UNID. Es el mayor riesgo | cliente |
| Ventana de la partida abierta para el cobro `_zlo` = 90 días (`try_customer`) | `ar_match.py:328,396` | Items de >90 días fuera del primer intento | cliente |
| FIFO "oldest-due prefix" / "prefix_skip" (`bankrec.py:1330-1367`) | supone que el cliente paga lo más antiguo | Con retrasos, saltarse facturas es más frecuente | cliente |
| Descuento por pronto pago: `discount_days + 3` | `bankrec.py:511`, `ar_match.py:219`, `remittance.py:345` | Es regla de POLICIES; con retrasos habrá menos `SHORT_DISCOUNT`, no fallo | política |
| Aviso: banco en `[min(pago,recepción)−10, max+5]` | `remittance.py:259-260` | Aviso con >10 días entre pago y recepción ⇒ no encuentra la línea (si no trae `pay_date`) | cliente |
| Aviso: tolera hasta 25 días entre aviso y asiento (`dd_>25 → continue`) | `bankrec.py:1020-1024` | idem (v3) | cliente |
| Avisos recibidos hasta `dmax + 30 días`; ficheros auxiliares `before=3, after=1` meses | `remittance.py:85`; `bankrec.py:519-523` | Aviso >30 días tras el pago no entra | cliente |
| Archivo DD/tarjeta/cheque/run ↔ banco: ±3 / 0..4 / 0..4 / ±5 días | `batches.py:380,463,545`; `bankrec.py:620,639,660,685,802-804` | Retraso bancario, no del cliente: sin cambio por morosidad | banco |
| Pares de anulación ±3 (con fecha referencia) / ±10 días | `nonitem.py:68` | Anulaciones tardías sin emparejar | banco |

Evidencia de que ya hay presión: UNID de cobros pasa de 6,7 % a 8,5 % en LIVE y, sin censura (jul-nov), de 6,7 a 7,3 %.

---

## 6. Riesgos ordenados (probabilidad × movimientos expuestos)

P = estimación mía (A 0,8-0,9 · M 0,4-0,6 · B 0,1-0,2). N = población expuesta en un semestre del tamaño de LIVE.

| # | Riesgo | P | N expuesto | Corrección propuesta (una línea) |
|---|---|---|---|---|
| 1 | Pagos más lentos agrandan el pool de partidas vencidas y superan los topes de búsqueda (`≤40`, `DP_MAX_POOL=80`, `MAX_NODES`, 400/200 días de edad, `_zlo` 90) | A (0,9) | ~25-30k cobros multi-partida + ~0,5k duplicados | Sustituir topes por poda por cliente con ampliación progresiva (más partidas solo si el pool pequeño no cierra) y validar retrasando +30/+60 días los pagos de dev 2025-04/05. |
| 2 | `masters_final` ausente/parcial/mal ruteado: silencioso (cae a `masters/`) o rotura (`FileNotFoundError`/`KeyError`); sin fx 2026 (v3) ⇒ FX/ZA01/renombrados mal | M (0,4) | Todo ZA01 + FX (~800 líneas/semestre) + clientes renombrados; si rompe, el 100 % | Verificación previa: exigir los 7 ficheros en `masters_final`, fallar con mensaje claro y volcar nº clientes/entidades/IBAN/fx_max antes de correr; fx con tope de antigüedad y aviso. |
| 3 | Banco/idioma nuevos de ZA01 (UMVC, MT940 inglés, ZAR, "KWI…") y el `fallback` convierte todo lo no resuelto en `UNIDENTIFIED`/`BANK_FEES` | A (0,8) | Líneas ZA01 (volumen desconocido; LIVE solo 2, ambas proformas) | Añadir regla de entidad/entidad-nueva: si la entidad no tiene items ni maestros históricos, emitir `UNIDENTIFIED` solo para cobros y `BANK_FEES` solo si las reglas lo dicen (resto: categoría por regla/importe), y cubrir en `nonitem_rules` el vocabulario sudafricano visible en LIVE. |
| 4 | Cobros multi-partida con aviso fuera de ventana (aviso >10-30 días después del pago) + censura de junio | M (0,5) | ~2k cobros de junio + multi-partida de entidades con 18-34 % sin aviso | Ampliar la ventana de avisos a la fecha del último dato de FINAL y casar por importe+referencia sin ventana si el aviso es único. |
| 5 | Cambio de cuenta de IT01: rutas ligadas a cuenta (lotes DD por importe, reembolsos, anulaciones, rechazos) | M (0,5) | Subconjunto de IT01 (8k líneas; DD/devoluciones/reembolsos ≈ cientos) | Emparejar por entidad (no por cuenta) como segunda pasada en lotes DD, reembolsos, anulaciones y rechazos; tratar la tabla de cuentas como incompleta. |
| 6 | Cachés por nombre de fase (`items_{ph}`, `bank_{ph}`, `open_start_{phase}_v2.pkl`) reutilizados con datos distintos | B (0,15) | Todo el run | Borrar `solution/cache/` y `solution/cache/v35/` antes de FINAL, o clave de caché con tamaño/mtime de los ficheros de origen. |
| 7 | Working tree ≠ `9d2adfd` (hook `passes_ledger` en `bankrec.py`) y `run_v35.sh` usa el working tree | M (0,5) | Todo v3 (paso 1) | Ejecutar FINAL desde un checkout limpio etiquetado (`git worktree add … 9d2adfd`) y confirmar `git status` limpio. |
| 8 | Clientes renombrados / IBAN nuevo: alias del maestro + nombres impresos de los últimos 200/400 días; IBAN único por cliente en v3 | M (0,5) | 165 clientes con >1 nombre impreso (0,3 %); ZA01 renombrada el 2026-04-01 | Indexar todos los nombres/IBAN históricos de `ar_invoices` sin límite de 400 días y los IBAN de avisos PDF también en v3. |
| 9 | Moncayo/formato: ya no hay quiebre; otros bancos que cambien de formato en 2026 | B (0,1) | ~13k líneas por banco medio | Sin cambio; vigilar `% UNID` por (banco, mes) en el informe de FINAL. |
| 10 | Listas de países (`SEPA_*`, `WH_*`) con `ZA` ausente y `entities.csv` sin ZA01 ⇒ `ec=""` ⇒ falso `SHORT_BANK_CHARGES` | B (0,2) | Cobros ZA01 con faltante pequeño | Derivar "mismo país" de `entities.csv`/cuenta y no etiquetar comisión si la entidad no está en el maestro. |

**Chequeo previo recomendado a la FINAL (sin código del pipeline)**: (a) `ls final/masters_final` con los 7 `.csv`;
(b) `git status` limpio en el commit etiquetado; (c) cachés borradas; (d) tras la corrida, tabla de % UNID por entidad×mes y
por banco×mes (se puede reproducir con el script del scratchpad
`/private/tmp/claude-501/-Users-mateolarareguera-Downloads-talky-ledger/78fbef1d-cf64-4209-a4ba-8c8e2eeb26a6/scratchpad/b.pkl`, que es un
volcado de LIVE × salida v3.5), y (e) lanzar `pipeline.py --sources` para tener el campo de explicación que hoy falta.
