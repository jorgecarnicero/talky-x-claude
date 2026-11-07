# Procedimiento FINAL (tronco model/v7-integ)

> **HUECO (17:12): `solution/tools/shift_report.py` (y su modo `--post`) NO está subido a `model/v3-harness`**
> (último commit allí: 9b109c7, anti-overfitting). Cuando la ventana 2 lo suba, se trae con cherry-pick y se rellenan
> los pasos marcados **[shift_report]** con los comandos exactos y qué hacer ante cada alerta. No se ha escrito nada
> de su uso para no duplicar ni adivinar su interfaz.

## Pasos

1. **Antes de lanzar — [shift_report] LIVE vs FINAL.** *Pendiente (ver hueco).*
2. **Lanzar A/B** (A = tronco con exc_overpay, B = `EXC_OVERPAY=0`, cadena v6c):
   ```bash
   PY=<venv>/bin/python solution/tools/final_ab.sh <hash> <carpeta_datos> <out_sin_espacios> final
   ```
   Decide solo si caben dos en paralelo (pico medido ~8,3 GB por ejecución; en 16 GB van en secuencia, ~13 min cada una).
3. **Después — [shift_report] `--post` sobre `<out>/A/sub.csv.gz` y `<out>/B/sub.csv.gz`.** *Pendiente (ver hueco).*
4. **final_check** sobre cada salida:
   ```bash
   python solution/tools/final_check.py --phase final --data <carpeta_datos> --sub <out>/A/sub.csv.gz
   python solution/tools/final_check.py --phase final --data <carpeta_datos> --sub <out>/B/sub.csv.gz
   ```
5. Subir A o B según la nota LIVE de v7 ov1 frente a ov0 (ver SUBMISSIONS.md).

**No usar `RUN_FINAL.sh`** (se niega sin `FORCE_ARYAN_RUNNER=1`; no ejecuta exc_overpay y usa rutas de otra máquina).
