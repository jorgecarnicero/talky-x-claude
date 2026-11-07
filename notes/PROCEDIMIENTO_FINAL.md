# Procedimiento FINAL (tronco model/v7-integ)

Orden estricto. **Una sola cosa pesada a la vez** (Mac de 16 GB; una ejecución completa llega a **8,3 GB** de pico).
Tiempos medidos en LIVE el 2026-11-07 con 2a8fd84 y caché vacía: ov0 (EXC_OVERPAY=0) 13 min 14 s, pico 8,3 GB;
ov1 (EXC_OVERPAY=1) 13 min 40 s, pico 7,1 GB. FINAL lee train+live+final, así que cuenta con algo más.

| Paso | Qué | Tiempo | Memoria |
|---|---|---|---|
| 0 | Variables y comprobaciones previas | 1 min | – |
| 1 | `final_check.py` previo (sin `--sub`) | ~1–2 min | ~4 GB (carga la fase) |
| 2 | `shift_report.py` LIVE vs FINAL | < 2 min | baja (solo stdlib) |
| 3 | `final_ab.sh` (A y B) | ~13 min cada una en LIVE → **~30–40 min en secuencia** en FINAL (estimado) | 8,3 GB pico **cada una**; en 16 GB van en secuencia |
| 4 | `shift_report.py --post` sobre A y B | **HUECO**: el modo `--post` no existe todavía (ac5cf5a no lo trae) | – |
| 5 | `final_check.py --sub` sobre A y B | ~1 min cada una | baja con `--skip-ledger` |
| 6 | Elegir A o B y subir **una vez** | – | – |

## 0. Variables y comprobaciones previas

```bash
cd ~/Downloads/talky-int && git fetch origin && git status -sb      # model/v7-integ, limpio, al día
export HASH=<commit candidato>                                       # lo fija el usuario
export DATA="$HOME/Downloads/talky-x-claude/Hackathon Claude Books/data"   # contiene masters/ train/ live/ y final/
export PY="$HOME/Downloads/talky-x-claude/Hackathon Claude Books/.venv/bin/python"   # final_ab.sh crea un enlace sin espacios
export OUT=$HOME/Downloads/final_ab_$(date +%H%M)                    # SIN espacios (run_final.sh no entrecomilla rutas)
export PYTHONHASHSEED=0
pgrep -fl 'pipeline.py|bankrec.py|docs_extract.py|dev.py' || echo "nada corriendo"   # tiene que decir "nada corriendo"
memory_pressure | tail -1                                            # >= 60 % libre antes del paso 3
df -h ~ | tail -1                                                    # >= 5 GB libres (cachés + CSV)
which pdftotext                                                      # obligatorio: live/final abortan sin él
```

Datos FINAL: descomprimir el archivo en `"$DATA/final/"` (con `final/masters_final/` dentro). Sin `masters_final`
completo, `run_final.sh` aborta (guard; `FINAL_STRICT_MASTERS=1` lo hace estricto).

## 1. Comprobación previa

```bash
"$PY" solution/tools/final_check.py --phase final --data "$DATA"
```

Tiene que acabar en `ALL CHECKS PASSED` (árbol limpio, cadena de `cascade.conf`, `masters_final`, `pdftotext`,
`PYTHONHASHSEED`, ledger frente a `open_items_at_start`). Si falla `masters_final` u `open_items_at_start` → **parar**:
son los datos, no el modelo.

## 2. Antes de lanzar: shift_report LIVE vs FINAL

`shift_report.py` lee `live/` y `train/` de cada carpeta, así que la FINAL se presenta como una vista con `live → final`:

```bash
V=$OUT/_view_final && mkdir -p "$V" && ln -sfn "$DATA/final" "$V/live" && ln -sfn "$DATA/train" "$V/train"
"$PY" solution/tools/shift_report.py "$DATA" "$V" | tee $OUT/shift_pre.txt     # A = LIVE (referencia), B = FINAL
```

Salida: como mucho 20 líneas `ok` / `ALERTA`; código de salida 1 si hay alguna alerta.

**Qué hacer si salta una alerta:**

- **ZA01 salta siempre** (entidad casi nueva: 2 cobros en LIVE, sin historia en TRAIN) en entidades / cuentas /
  divisas nuevas y en "% contrapartes sin historia en TRAIN". **Es esperada, no es un fallo: seguir.**
- Cualquier **otra** alerta → **parar** y:
  1. mirar en `shift_pre.txt` qué medida y qué entidad / cuenta / divisa la provoca y cuántos movimientos lleva;
  2. si son pocos movimientos (< 1 % del total) → seguir y anotarlo;
  3. si es grande, decidir antes del paso 3: lanzar igualmente A/B (no hay tiempo de cambiar el modelo) y vigilar esa
     entidad en el paso 5; si la alerta toca formatos de aviso de remesa o referencias, esperar más UNIDENTIFIED allí.
  Apuntar la decisión en `$OUT/decision.txt`.

## 3. Lanzar A/B

```bash
solution/tools/final_ab.sh "$HASH" "$DATA" "$OUT" final --dry-run     # comprobaciones previas, no lanza nada
solution/tools/final_ab.sh "$HASH" "$DATA" "$OUT" final               # A = cascade.conf (EXC_OVERPAY=1), B = EXC_OVERPAY=0
```

- Decide solo si caben dos en paralelo (`PEAK_GB=9` por ejecución; necesita ~20 GB). En 16 GB → **A y después B**.
- Cada una sale de su propio checkout limpio de `$HASH` y su propia caché vacía, con `PYTHONHASHSEED=0`.
- Salidas: `$OUT/A/sub.csv.gz`, `$OUT/B/sub.csv.gz`, logs `$OUT/{A,B}/run.log`, horas en `$OUT/times.txt`,
  `validate.py` en `$OUT/{A,B}/validate.txt` y la tabla en `$OUT/summary.txt` (filas, líneas sin respuesta, pares
  duplicados, % UNIDENTIFIED por entidad, filas que difieren entre A y B).
- Si una de las dos falla, el guion acaba con código ≠ 0 y la otra sigue siendo utilizable. **No relanzar a ciegas:**
  mirar las últimas 30 líneas de `run.log`.
- **No usar `RUN_FINAL.sh`**: se niega a correr sin `FORCE_ARYAN_RUNNER=1`, no ejecuta `exc_overpay` y usa rutas de otra máquina.

## 4. Después: shift_report --post sobre A y B

> **HUECO:** el modo `--post` no está en ac5cf5a (único commit de `shift_report.py` en `model/v3-harness`, 17:09).
> Cuando la ventana 2 lo suba: cherry-pick a `model/v7-integ` y escribir aquí el comando para `$OUT/A/sub.csv.gz` y
> `$OUT/B/sub.csv.gz`. Mientras tanto, la tabla de UNIDENTIFIED por entidad de `summary.txt` y del paso 5 cubre la
> parte de salida (ZA01 alta = esperado).

## 5. Comprobación posterior

Las dos salidas se comprueban **desde el checkout limpio de A** (el de B tiene `cascade.conf` cambiado a propósito, y
`final_check` exige árbol limpio):

```bash
cd $OUT/src_A
PYTHONHASHSEED=0 "$PY" solution/tools/final_check.py --phase final --data "$DATA" --sub $OUT/A/sub.csv.gz
PYTHONHASHSEED=0 "$PY" solution/tools/final_check.py --phase final --data "$DATA" --sub $OUT/B/sub.csv.gz --skip-ledger
shasum -a 256 $OUT/A/sub.csv.gz $OUT/B/sub.csv.gz | tee $OUT/shasum.txt
```

Tiene que salir `VALID`, todas las líneas cubiertas y 0 pares duplicados. UNIDENTIFIED por entidad: ZA01 alta es
esperado; otra entidad muy por encima de su valor en LIVE → mirar si coincide con una alerta del paso 2.

## 6. Elegir y subir

- A si la nota LIVE de **v7 ov1** (EXC_OVERPAY=1) supera a **v7 ov0** (EXC_OVERPAY=0); si no, B (ver `SUBMISSIONS.md`).
- Subir **una sola vez** el fichero elegido y apuntar en `SUBMISSIONS.md`: hash, A/B, ruta y `shasum`.

## Anexo: significancia de un candidato en dev (no aplica a FINAL: no hay golden)

```bash
"$PY" solution/tools/significance.py dev_runs/<tronco>/sub.csv.gz dev_runs/<candidato>/sub.csv.gz \
      --data ~/hackdata/data_dev [--json out.json]
```

Las dos `sub.csv.gz` tienen que ser de la **misma ventana**. Imprime G1 (McNemar: mal→bien c, bien→mal b, z),
G2 (signo por pagador), G3 (balance por mes y por entidad > 1.000 movimientos) y el veredicto. Código de salida
0 / 1 / 2 = ACEPTAR / NEUTRO / RECHAZAR. Detalle de las puertas en `notes/ANTI_OVERFIT.md`.
