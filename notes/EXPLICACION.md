# Conciliación bancaria: qué hace el sistema y cómo se evaluó

Rama de entrega: `model/v6c-merge` (v6b de Aryan + `exc_overpay`). El comando oficial es `solution/run_final.sh live|final`;
la cadena de pases y las variables de entorno están **en un único fichero**, `solution/cascade.conf`, que leen
`run_final.sh` y `solution/tools/dev.py` (así dev, LIVE y FINAL ejecutan lo mismo).

## 1. Qué hace el sistema

Base: el matcher v3 (`solution/bankrec.py`) produce una respuesta para cada movimiento. Sobre ella corre una cascada de pases
de alta precisión sobre **un único libro de partidas abiertas** compartido: cada pase reclama solo las líneas que explica con
evidencia y una línea reclamada por un pase anterior no la vuelve a tocar. Lo que nadie reclama lo contesta v3.

Variables (`cascade.conf`): `EXC_R5=1` (reglas R5 de `exc_fix`), `PAYER_DIR=1` (directorio de pagadores construido con el
histórico), `AR_DUST=1` (filtro de restos mínimos en `ar_match`). `PYTHONHASHSEED=0` lo fija `run_final.sh`.

Cadena, en orden de ejecución:

1. `batches`: líneas cuyo contenido figura exacto en un fichero auxiliar (remesas de adeudos, liquidaciones de tarjeta, depósitos de cheques, ejecuciones de pago); las devoluciones pasan a RETURNED.
2. `remittance`: casa cada aviso de remesa con su cobro (entidad, importe, referencia o nombre del pagador) y asigna los documentos que lista, con deducciones y su motivo.
3. `ap_match`: líneas de pagos a proveedores no cubiertas por ficheros de ejecución (adeudos de proveedor, abonos de proveedor, transferencias que citan facturas).
4. `nonitem`: líneas que no liquidan ninguna partida y reciben categoría (anulaciones de banco, impuestos, nóminas, préstamos…) por palabras clave y familias de código bancario.
5. `ar_multi`: cobros que compensan notas de crédito de devolución: el importe es la suma de facturas menos las notas relacionadas.
6. `ar_pdf`: cobros cuyo importe lo explica una cláusula del PDF de la factura (retención, cuotas, factura sustituida), con evidencia del pagador.
7. `v3import`: importa las respuestas de v3 con partidas para las líneas aún no reclamadas, descartando partidas que un pase anterior ya liquidó en el libro.
8. `ar_match`: identifica al pagador, interpreta las referencias y explica el importe sobre las partidas abiertas del cliente (cronológico, FIFO del libro).
9. `ar_multi_late`: cobros n-a-m aún sin explicar: varias facturas completas más una pagada en un 40/50/60 % (PARTIAL).
10. `ar_bigpool`: clientes bien identificados con muchas facturas abiertas que pagan un lote sin referencia utilizable: tramo contiguo por vencimiento, con algunas omitidas.
11. `v3rest`: respuesta de v3 para cada línea que sigue sin reclamar.
12. `exc_fix`: recalcula excepciones e importes de filas ya contestadas según POLICIES (duplicado que en realidad era un pago normal, parcial/resto, retención, nota de crédito neteada, sobrepago sin referencia R5).
13. `exc_overpay`: sobrepago de una única factura en cobros aún UNIDENTIFIED: exceso redondo (1 o 2 unidades, múltiplo de 5) o 1 %/10 % de la partida, con pagador identificado y una sola partida candidata; amount = importe abierto.
14. `cancel_guard`: elimina filas de facturas anuladas por nota de crédito y de las notas que las anulan (nunca se liquidan por banco).

## 2. Cómo se evaluó

- **Dev**: ventana de abril y mayo de 2025 construida con TRAIN en formato LIVE (`solution/tools/dev.py`), pipeline sin cambios con `--phase live`, puntuada con el scorer oficial contra el golden. La tabla de `SUBMISSIONS.md` indica que dev queda a unos 0,7 puntos de LIVE.
- **Regla de aceptación de un cambio**: gira al menos 30 líneas a mejor de forma neta, la nota global sube y ningún segmento de más de 90 % baja más de 0,5. Las reglas son generales: sin ids, fechas ni importes concretos, ni el campo `segment`.
- **LIVE solo confirma**: no se ajustan umbrales con la nota de LIVE; los envíos los hace el equipo por rondas.
- Medición propia de `exc_overpay` sobre v6a en dev (arnés `dev.py`): 93,79 → 94,33; 55 líneas de mal a bien y 2 de bien a mal; el segmento `ar_overpayment` pasa de 0,533 a 0,941. (Esta medición usa el arnés de dev local y no coincide exactamente con la columna Dev de `SUBMISSIONS.md`.)

## 3. Historia de notas (LIVE, de `SUBMISSIONS.md`)

| Modelo | LIVE | Qué añade |
|---|---:|---|
| v3 | 82,25 | bloques A y B: duplicados, devoluciones, categorías por texto, retenciones, gastos bancarios |
| v3.5 | 91,92 | v3 + pases de alta precisión (batches, remittance, AP, nonitem) + matcher AR sobre lo no identificado por v3 |
| v4-cascade | 92,67 | v3.5 + `ar_pdf`, `ar_multi`, `exc_fix` (+R5), `cancel_guard`, `payer_dir` |
| v6a | 92,81 | v4-cascade + paso n-a-m de `ar_match` |
| v6b | 93,31 | v6a + `ar_bigpool`, `exc_fix` R5 v2 (sobrepago 56 % → 76 %), filtro de restos |
| bc030cc (v6a + `exc_overpay`) | 93,24 | dato facilitado por el equipo; aún no figura en `SUBMISSIONS.md` |
| v6c (v6b + `exc_overpay`) | pendiente | CSV de LIVE generado (`sub_live_v6c.csv.gz`); aún sin nota |

## 4. Riesgos de la FINAL y cómo se mitigan

- **Entidad nueva (ZA01)**: en LIVE aparece con muy pocos cobros, así que está casi sin probar. `solution/tools/final_check.py` imprime el % de UNIDENTIFIED por entidad para detectar una entidad que se desvíe, y las partidas que solo existen en `open_items_at_start` (saldos de una filial adquirida) se cargan en el libro; `final_check` comprueba que todas las filas de `open_items_at_start` están cargadas con su importe abierto.
- **`masters_final`**: la FINAL usa `final/masters_final/`. `src/guards.py` aborta con error claro si falta la carpeta, o si falta alguna tabla o columna respecto a `masters/` (nunca cae en `masters/` sin avisar). El calendario de la fase se lee de los propios datos, no está fijado en el código.
- **`pdftotext`**: sin él se saltarían en silencio los avisos de remesa en PDF. En LIVE y FINAL se aborta con error claro; en dev solo se avisa.
- **Tipo de cambio**: sin tipo para una fecha se usa el último anterior y, si no hay, el primero posterior; nunca `None` para una divisa conocida. Verificado idéntico al comportamiento anterior en todos los días de la tabla de LIVE.
- **Reproducibilidad**: `PYTHONHASHSEED=0` fijado por `run_final.sh`; `final_check.py` comprueba árbol de git limpio, que `cascade.conf` coincide con la cadena de `run_v6b.sh` más `exc_overpay`, validación del CSV y que no falta ningún movimiento ni hay pares duplicados.
