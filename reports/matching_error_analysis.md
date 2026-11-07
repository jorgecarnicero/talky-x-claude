# ar_ref_noise — análisis de 30 fallos reales (dev 2025-04..05)

**Contexto.** El diagnóstico se hizo al empezar, sobre el motor que entonces era la base (team v3, `model/v3` @ `fa9ed69`,
dev 82.87 con `--phase dev`), antes de que el equipo moviera la base a `aryan/matcher` (v3.5). Las causas siguen siendo
válidas para v3.5: sus fallos restantes de `ar_ref_noise` son, en su mayoría, líneas que acaban `UNIDENTIFIED` en
`v3rest` (ver la última sección).

- Herramienta: `solution/tools/matching_diag.py` (rama `model/v3-matching-on-v3`, commit WIP local), muestra
  **reproducible** (semilla 7) y **estratificada por el matcher** que produjo la respuesta errónea.
- El golden se usa **sólo para diagnosticar** (saber qué pagador/facturas eran los correctos); los índices consultados
  son los que el motor construye al inicio de la ventana.
- En v3, `ar_ref_noise` en dev: 14 940 líneas, 78.0 % de acierto, **3 280 fallos**; 3 130 (95 %) acabaron en la
  categoría por defecto `UNIDENTIFIED` → el problema es de **recall** (identificar al pagador / explicar el importe), no
  de precisión.

## Distribución

| Tipo de error | Casos (muestra 30) | % muestra | Todos los fallos (3 280) | Causa técnica | Posible solución |
|---|---:|---:|---:|---|---|
| 1. Nombre truncado | 5 | 16.7 % | 559 (17.0 %) | El texto corta palabras ("AQUA" por AQUATICS, "HUGHE" por HUGHES); el índice sólo acepta tokens exactos | Coincidencia por prefijo de palabra y truncado en orden, ponderado por IDF |
| 2. Nombre comercial ≠ legal | 2 | 6.7 % | 214 (6.5 %) | El banco muestra el nombre comercial; v3 mezcla todos los tokens de todas las variantes en una bolsa y diluye la cobertura | Puntuar cada variante (legal, comercial, documentos) por separado y quedarse con la mejor |
| 3. Empresa renombrada | 0 | 0 % | 18 (0.5 %) | Nombre antiguo sólo en facturas antiguas | Variantes de documentos con su fecha de emisión (sin usar documentos posteriores a la línea) |
| 4. Matriz o filial | 0 | 0 % | 65 (2.0 %) | Paga otra empresa del grupo | Probar el grupo (`group_id`) sólo con pagador fuerte |
| 5. IBAN distinto / histórico | 0 | 0 % | 1 (0.0 %) | IBAN no en el maestro | Historial IBAN → pagador desde líneas etiquetadas anteriores |
| 6. Varias facturas del mismo importe | 0 | 0 % | 4 (0.1 %) | — (v3 ya elige FIFO / mes de servicio) | — |
| 7. Referencia incompleta / ambigua (pagador correcto, importe no explicado) | 8 | 26.7 % | 1 067 (32.5 %) | Pagos de 3–38 facturas que no son un prefijo por antigüedad; la búsqueda de combinaciones de v3 llega a 4 | Búsqueda exacta de subconjuntos acotada (ya resuelta en v3.5 por `ar_match`) |
| 8. Otros: homónimos / ranking | 15 | 50.0 % | 1 352 (41.2 %) | El pagador correcto está en la lista pero en posición 2–12 (otros con nombres parecidos: "Pool Supply …", "Landscapes Santa Lucia Springs LLC" vs "… Co."); v3 sólo prueba los candidatos a ≤ 0.25 del mejor y descarta la forma jurídica | Probar varios pagadores y dejar que el importe desambigüe con margen; forma jurídica como evidencia; historial de alias |

Clasificación por reglas deterministas (orden: 7/6 si el pagador correcto era el primero; después 4, 3, 2, 1, 5, 8).
Los tipos 3–6 tienen muy pocos casos: su porcentaje es estadísticamente poco fiable.

## Ejemplos anonimizados

| Tipo | Texto bancario (anonimizado) | Correcto | Por qué falló v3 |
|---|---|---|---|
| 1 | `ACH CREDIT PORT W**** AQUA CCD PAYMENT ID:…` / cp `Port W**** Aquatics Co.` | 1 factura del cliente | El pagador correcto quedó en la posición 11 de 466: "AQUA" no coincide con AQUATICS |
| 1 | `DIRECT CREDIT OUTDOOR LIVING HUGHE INVOICE INV25-…` | 1 factura | posición 108 de 160: palabra cortada + nombres genéricos ("OUTDOOR LIVING") |
| 2 | `TRANSFERENCIAS G** RIEGOS \| G** RIEGOS FRA …` | 1 factura | el texto es el nombre comercial; la bolsa de tokens mezclada lo puntúa segundo |
| 7 | `SEPA-UEBERWEISUNG … SVWZ+VIREMENT` / cp `M****** D******* Aquatique EURL` | 12 facturas | pagador correcto, pero 12 facturas no consecutivas: fuera del alcance de combinaciones ≤ 4 |
| 8 | `ACH CREDIT LANDSCAPES SANTA CCD …` / cp `Landscapes Santa L**** Springs Co.` | 1 factura | existe un cliente casi homónimo "… LLC"; v3 descarta la forma jurídica y empata |
| 8 | `ACH CREDIT POOL SUPPLY SUN CCD …` / cp `Pool Supply Sun P****** Hills Corp.` | 3 facturas | pagador correcto segundo de 516; sólo se prueba el primero |

## Impacto y riesgo (antes de escribir reglas)

- **Mayor impacto:** tipo 8 (41 %) + 1 (17 %) + 2 (6.5 %) = **~65 % de los fallos** se deben a la identificación del pagador.
  Se pueden atacar **sin perder precisión** si el pagador sólo se acepta cuando el importe lo confirma y no hay otro
  pagador plausible que también lo explique (margen), y si los alias sólo vienen de datos anteriores a la línea.
- **Tipo 7 (32 %):** importe; en v3.5 ya lo cubre `ar_match` (subset-sum con DP / meet-in-the-middle).
- **Riesgo de falsos positivos:** el segmento `unidentified_lookalike` (nombres parecidos a clientes que no lo son, 478
  líneas al 99 %) es el que hay que vigilar: forma jurídica distinta penaliza, y nunca se fuerza un match sin importe.

## Qué queda en la base actual (v3.5, `aryan/matcher`, dev 92.87)

| Segmento | Fallos | v3rest → UNIDENTIFIED | `ar_match` ítems erróneos | v3 ítems erróneos |
|---|---:|---:|---:|---:|
| ar_ref_noise | 691 | 372 | 201 | 116 |
| ar_1toN_credit_note | 360 | 130 | 104 | 126 |
| ar_same_amount_month | 45 | 23 | 4 | 18 |

El pase `ar_payer` (model/v5-matching) ataca la primera columna: ver `reports/matching_v5_results.md`.
