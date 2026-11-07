# Prueba de estrés (medida 5): entidad sin historia en TRAIN — escenario ZA01

**Estado: NO ejecutada.** Condición de arranque no cumplida a las 17:12 (había una ejecución de LIVE en marcha:
`sub_live_v7_ov1`, pico 8,3 GB por ejecución en un Mac de 16 GB). Este documento es el diseño, listo para lanzar.

## Por qué

ZA01 tiene **2 cobros en LIVE** y prácticamente ninguno en TRAIN; si en FINAL pesa más, todo lo que se aprende de la
historia etiquetada no existe para ella. Hay que saber cuánto cae la nota y cuánto sube el UNIDENTIFIED de una entidad así.

## Qué se aprende de TRAIN (y por tanto desaparece para una entidad nueva)

| Pieza | De dónde lo aprende | Efecto si falta |
|---|---|---|
| `src/payer_dir.py` (`PAYER_DIR=1`) | `train/golden_allocations` AR + `train/bank_transactions`: IBAN y nombre del pagador → cliente, por entidad | sin IBANs ni alias aprendidos; solo `customers.csv` (nombre actual + un IBAN) |
| `src/ar_payer.py` / alias en `ar_index` | historial de nombres / IBAN | menos pagadores identificados → más UNIDENTIFIED |
| `solution/bankrec.py` (backbone v3, pase `party`) | historial de pagos por pagador | menos `exact` / `comb*` por pagador |
| caché `golden_train.parquet`, `payer_dir_<cutoff>.parquet` | se construyen desde lo anterior | **usar caché nueva**, si no se filtra la historia |

## Perturbación (una sola)

Entidad objetivo: **FR01** (2.513 cobros en la ventana dev 2025-04..05; tamaño medio, texto bancario no inglés).
Alternativa si se quiere algo más pequeño: CH01 (856).

Copia `data_dev_stress_FR01/` de `Hackathon Claude Books/data_dev_base6c/` (ventana 2025-04-01..2025-05-31):

- Todo enlazado (symlink) al original **salvo** `train/golden_allocations.csv.gz` y `train/bank_transactions.csv.gz`,
  que se escriben nuevos (nunca se modifica el original ni las copias en uso).
- `train/golden_allocations.csv.gz`: quitar las filas cuyo `bank_txn_id` sea de FR01.
- `train/bank_transactions.csv.gz`: poner a vacío `counterparty_name`, `counterparty_account`, `remittance_info` de
  las líneas FR01 (para que ningún pase reconstruya historia a partir del texto de TRAIN). `live/`, `masters/` y
  `open_items_at_start` intactos: el saldo inicial no depende de la historia.
- Coste estimado: < 5 min (solo dos ficheros gz). Si pasa de 15 min → abortar.

## Ejecución (una sola, aislada)

```bash
# con BANKREC_CACHE nuevo, PYTHONHASHSEED=0, en un checkout limpio de 2a8fd84
python solution/tools/dev.py --name stress_FR01 --out <data_dev_stress_FR01>     # tronco tal cual (cascade.conf);
# dev.py ya fija PYTHONHASHSEED=0 y una caché por ventana (BANKREC_DATA/BANKREC_CACHE); --out apunta a la copia
```

Referencia "tronco normal": la misma ventana sin perturbar (`data_dev_base6c`) con 2a8fd84 — reutilizar el resultado
de dev ya existente si lo hay para ese hash; si no, es una segunda ejecución (no incluida en esta prueba).

## Qué medir

1. Nota dev global con y sin FR01 en la historia.
2. % UNIDENTIFIED de los cobros FR01 (y del resto, para ver que no se mueve).
3. Por segmento de FR01: qué pase pierde más claims (`sub_*_src.csv`: columna de origen por línea) — candidatos a frágil:
   `payer_dir`/`ar_match` por alias, `ar_bigpool` (necesita pagador único por IBAN/alias), `ar_multi_late`
   (pagador por nombre exacto), backbone `party`. Los pases por referencia (`refs`, `remittance`, `ar_pdf`) deberían
   aguantar.

## Decisión que alimenta

Si UNIDENTIFIED de la entidad sin historia se dispara (p. ej. > 2× el de las demás), en FINAL vigilar ZA01 con
`final_check.py` (tabla de UNIDENTIFIED por entidad) y considerar un fallback por `customers.csv` para esa entidad.
