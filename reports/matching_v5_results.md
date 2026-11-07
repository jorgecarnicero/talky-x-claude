# model/v5-matching — resultados

## 1. Rama y commit base

- Rama de trabajo: **`model/v5-matching`** (publicada en `origin`).
- Base: **`origin/aryan/matcher` @ `d0dddfb`** (2026-11-07 12:47, "FINAL readiness…"), el v3.5 del equipo (LIVE 91.92).
  La base cambió a mitad de trabajo (antes era `model/v3` @ `fa9ed69`); el trabajo sobre v3 queda en la rama local
  `model/v3-matching-on-v3` (commit WIP, no publicada) y sólo se ha portado lo que encaja en la nueva arquitectura.
- Arnés: **`model/v3-harness` @ `6baa932`**, sin modificar (`make_dev_dataset.py` + `dev.py --score-only`, scorer
  oficial), ventana dev **2025-04-01..2025-05-31**, arranque *clean* (`open_items_at_start` simulado, TRAIN cortado en
  la fecha de inicio). La ventana sellada (2025-01..02) **no** se ha usado.

## 2. Diagnóstico inicial (30 fallos de ar_ref_noise)

Ver `reports/matching_error_analysis.md`. ~65 % de los fallos eran de identificación del pagador (homónimos /
ranking 41–50 %, truncados 17 %, nombre comercial 6.5 %); ~32 % pagador correcto pero importe no explicado
(subconjuntos, ya cubierto en v3.5 por `ar_match`).

## 3. Problemas encontrados

- `src/data.py` de `aryan/matcher` abría las facturas nuevas desde la fecha fija `2025-07-01`: en la ventana dev
  (abril–mayo) no se abrían las facturas emitidas dentro de la ventana. Corregido con la misma línea que usa el arnés
  (fecha de la primera línea bancaria; idéntico en LIVE/FINAL). **Coordinar con Aryan.**
- En v3.5 quedan 2 868–3 002 líneas que acaban en `v3rest` como `UNIDENTIFIED`; ~54 % tenían en realidad facturas.
- Rendimiento en esta máquina (Windows, 16 GB): el pase `batches` tarda ~16 min y el pipeline reserva ~10 GB
  (≈5 GB residentes). Con otro proceso pesado en paralelo, Windows pagina.

## 4. Reglas implementadas (`src/payer_v5.py`, `src/passes/ar_payer.py`)

Pase **`ar_payer`**, entre `ar_match` y `v3rest`, sólo para cobros (`amount > 0`) que nadie ha explicado:

1. **Normalización**: Unicode/acentos, mayúsculas, formas jurídicas con puntos ("S.p.A." → SPA, "Sp. z o.o."),
   forma jurídica separada del nombre, abreviaturas genéricas (HNOS, CIA, INTL…). Sin nombres concretos.
2. **Variantes de nombre puntuadas una a una** (no una bolsa de tokens): nombre maestro, nombre comercial, nombres
   impresos en documentos (sólo desde su `issue_date` ≤ fecha de la línea), nombres de pagador vistos en líneas
   bancarias anteriores.
3. **Truncados**: prefijo de palabra (≥ 3 letras, peso 0.75) y truncado en orden ("OUTDOOR LIVING HUGHE"), siempre
   ponderado por IDF: palabras frecuentes ("POOL", "SERVICE", "SERVICIOS") no bastan.
4. **Forma jurídica**: nombre completo con su forma jurídica +0.35; forma distinta justo detrás del nombre −0.4
   (separa "… Springs LLC" de "… Springs Co.").
5. **IBAN**: IBAN del maestro (diluido si lo comparten varios clientes) e **historial IBAN → cliente** con pureza.
6. **Alias exactos** (nombre de contrapartida o primeras palabras del texto) → cliente, con pureza.
7. **Historial sin fuga temporal**: sólo golden de TRAIN de líneas con `booking_date` < primera línea del periodo
   (dev: TRAIN ya cortado por el arnés; LIVE/FINAL: todo TRAIN). Nunca se leen etiquetas del periodo evaluado.
8. **Decisión** (importe con la lógica de `ar_match`, `Matcher.try_customer`, sin duplicarla): hasta 10 pagadores con
   puntuación ≥ 0.55. Se acepta si
   - el mejor pagador explica el importe, puntuación ≥ 0.80 y ningún otro pagador que también lo explique a < 0.10
     (`top`); o
   - un pagador explica el importe con explicación **exacta** (`single`, `ref_exact`, `ref_cn`), puntuación ≥ 0.75
     y ventaja ≥ 0.25 sobre el siguiente que lo explique (`amt`).
   Las reglas de parcial por fracción / sobrepago sólo se activan para el pagador `top`.
9. **Grupo / matriz**: lo resuelve ya `ar_match` (miembros del `group_id`); no se ha duplicado.
10. **Mes de servicio y credit notes**: los resuelven `ar_match` (PDF `service_month`, unidades factura+abono,
    `ref_cn`) y `pdfinfo`; `ar_payer` los reutiliza al delegar el importe. La interfaz `pdf_facts(item_id)` de la
    rama de PDFs no es necesaria en esta arquitectura (los hechos de PDF llegan por `src/pdfinfo.py`).

Ningún ID, cliente, nombre, IBAN, fecha concreta, respuesta golden ni el campo `segment` se usa en inferencia.

## 5. Archivos

| Archivo | Cambio |
|---|---|
| `src/payer_v5.py` | **nuevo**: normalización, historial, `PayerIndex` |
| `src/passes/ar_payer.py` | **nuevo**: pase |
| `src/pipeline.py` | `ar_payer` en `PASS_ORDER` (1 línea) |
| `run_v35.sh` | `ar_payer` en la lista de passes (1 línea) |
| `src/data.py` | apertura de facturas nuevas desde el inicio del periodo (1 línea, fix de arnés) |
| `tests/test_payer_v5.py` | **nuevo**: 18 tests |
| `tools/run_dev.py` | **nuevo**: ejecuta esta rama sobre el dataset del arnés y puntúa con `dev.py --score-only` |
| `tools/drop_claims.py` | **nuevo**: deriva la salida sin (parte de) un pase — `v3rest` no usa el ledger, así que es exacto |
| `reports/*.md` | informes |

No se ha tocado ningún pase de otros compañeros (batches, remittance, ap_match, nonitem, ar_match, v3import, PDFs).

## 6. Tests

`python -m unittest discover -s tests` → **18 OK**: normalización (acentos, formas jurídicas, "SPA" dentro del nombre),
truncados, truncado en orden, nombre comercial, renombrada vía documentos antiguos, **fuga temporal** (nombre de un
documento posterior a la línea no se usa; historial ignora líneas desde el inicio del periodo), IBAN histórico, IBAN
compartido diluido, alias exactos, otra entidad ignorada, palabras comunes insuficientes, línea con campos vacíos,
determinismo, reglas de decisión.

## 7–8. Puntuación (dev 2025-04..05, scorer oficial, mismo arnés y dataset)

| Métrica global | Antes (base `aryan/matcher`) | Después (`+ar_payer`) | Diferencia |
|---|---:|---:|---:|
| **Puntuación** | **92.87** | **92.99** | **+0.12** |
| Allocation F1 | 0.9440 | 0.9445 | +0.0005 |
| Allocation precision | 0.9771 | 0.9766 | −0.0005 |
| Allocation recall | 0.9131 | 0.9144 | +0.0013 |
| Transaction accuracy | 0.9622 | 0.9639 | +0.0017 |
| Exception macro-F1 | 0.8403 | 0.8427 | +0.0024 (UNIDENTIFIED 0.705 → 0.726, FX 0.847 → 0.849) |
| Tiempo pipeline (esta máquina) | 1 874 s | 2 009 s | +135 s (`ar_payer` 223 s, índice 169 s) |
| Memoria | ~5 GB residentes / ~10 GB reservados | igual (+índice de nombres, < 0.5 GB) | — |

La base reproduce exactamente el baseline publicado del arnés (92.87). Comprobación de la derivación: quitar todas
las líneas de `ar_payer` con `tools/drop_claims.py` da 92.87 exactos.

| Segmento | n | Antes | Después | Diferencia |
|---|---:|---:|---:|---:|
| ar_ref_noise | 14 940 | 95.37 % | 95.86 % | +0.48 (+72 líneas) |
| ar_same_amount_month | 1 422 | 96.84 % | 98.03 % | +1.20 (+17) |
| ar_1toN_credit_note | 1 079 | 66.64 % | 67.10 % | +0.46 (+5) |
| ar_same_amount_fifo | 2 024 | 95.95 % | 96.44 % | +0.49 (+10) |
| ar_partial | 1 333 | 97.30 % | 97.75 % | +0.45 (+6) |
| ar_fx / ar_instalment / ar_dd_represented_transfer / ar_third_party_agency | | | | +1 a +2 líneas cada uno |
| **Resto de segmentos** | | | | **sin cambios (ninguno baja)** |

`ar_payer` reclama **134 líneas**, todas mal en la base (eran `UNIDENTIFIED` de `v3rest`); **117 exactas (87.3 %)**:
`amt` 43/43, `top` 74/91. Las 17 erróneas: 13 en `subset_dp_prim_m`, 2 `single`, 1 `refplus`, 1 `subset_l`.
Probado (offline, exacto) excluir `subset_dp_prim_m/l`: **92.97 < 92.99** → no se aplica (sólo mejoras demostradas).

## 9. Precisión de allocation

0.9771 → 0.9766 (−0.05 pt) a cambio de +0.13 pt de recall y +0.17 pt de txn accuracy; la puntuación global sube.
Las 17 líneas erróneas que añade `ar_payer` ya estaban mal antes (UNIDENTIFIED); ahora además añaden filas de
asignación incorrectas.

## 10. Incertidumbre y riesgos de sobreajuste

- Ganancia pequeña (+0.12 ≈ 134 líneas de 69 553). Es determinista en esta ventana, pero con una sola ventana no hay
  intervalo de confianza; las clases con < 100 filas no son fiables.
- Umbrales (0.55 / 0.80 / 0.75 / márgenes) fijados a priori desde el experimento sobre la base v3; en dev sólo se
  probó **una** alternativa (excluir subset DP), que se descartó. No se usó LIVE ni la ventana sellada.
- **Deriva temporal del historial**: en dev el historial llega hasta el día antes de la ventana; en LIVE (jul–dic 2025)
  el historial de TRAIN tiene 0–6 meses y en FINAL (2026) 6–12 meses → la parte de alias/IBAN aportará algo menos.
  Los nombres de maestro/documentos no dependen de eso.
- Riesgo de *lookalikes*: `unidentified_lookalike` no cambió (0 líneas reclamadas allí).

## 11. Dependencias pendientes

- PDFs: no hay dependencia de `pdf_facts(item_id)`; los hechos de PDF ya llegan vía `src/pdfinfo.py` a `ar_match`,
  que `ar_payer` reutiliza.
- Arnés: el fix de `src/data.py` debe acordarse con Aryan (es el mismo que ya lleva `model/v3-harness`).
- La medición de memoria de `tools/run_dev.py` medía el lanzador del venv; corregido para medir el árbol de procesos
  (pendiente de usar en una ejecución nueva).

## 12. Commits

Ver `git log origin/aryan/matcher..model/v5-matching`.

## 13. Integración

```
git fetch origin
git checkout <rama de integración>            # la que decida el integrador (model/v3 está congelada tras su envío)
git merge --no-ff origin/model/v5-matching     # o rebase; conflictos posibles sólo en src/pipeline.py, run_v35.sh, src/data.py (1 línea cada uno)
python -m unittest discover -s tests
python tools/run_dev.py --name integ --passes batches,remittance,ap_match,nonitem,v3import,ar_match,ar_payer,v3rest
```
Aceptar si la puntuación dev ≥ la de la rama de integración sin `ar_payer` y ningún segmento baja > 0.5.
LIVE: `./run_v35.sh live` (ya incluye `ar_payer`) → `validate_submission.py` → el integrador sube y registra en
`SUBMISSIONS.md`.
