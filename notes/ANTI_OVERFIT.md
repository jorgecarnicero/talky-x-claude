# Método anti-sobreajuste

Nuestro ciclo: **dev decide, LIVE confirma, la ventana sellada se usa una sola vez**. Este documento fija las puertas
que tiene que pasar un candidato (un pase nuevo o un cambio de regla) antes de entrar en el tronco, y explica por qué
existe cada una. La herramienta `solution/tools/significance.py` mide G1, G2 y G3. G4–G7 son disciplina de proceso y
quedan registradas en `SUBMISSIONS.md`.

Tamaños: dev tiene n = 69.553 movimientos (2025-04 y 2025-05) y LIVE n = 203.083. La ventana sellada es 2025-01-01..2025-02-28
(`dev.py --sealed`). Las ganancias típicas de un candidato son de 30 a 300 movimientos en dev, es decir, entre 0,04 y 0,4
puntos de acierto por movimiento.

## 1. Fuentes leídas

Solo aparece lo que se leyó de verdad. Los PDF se descargaron y se pasaron por `pdftotext`.

| Fuente | Qué se leyó | Qué usamos |
|---|---|---|
| Blum y Hardt, *The Ladder: A Reliable Leaderboard for Machine Learning Competitions*, [arXiv 1502.04585](https://arxiv.org/abs/1502.04585) | PDF completo: §3 (Fig. 1, Teorema 3.1), §4 (Ladder sin parámetros, Fig. 2, §4.1), §5–6 (ataque de boosting, datos reales de Kaggle) | Publicar una mejora solo si es significativa; prueba emparejada; la cota es de peor caso |
| Dwork, Feldman, Hardt, Pitassi, Reingold y Roth, *Generalization in Adaptive Data Analysis and Holdout Reuse*, [arXiv 1506.02629](https://arxiv.org/abs/1506.02629) | PDF completo: §1.1.1, §4 (Thresholdout, Fig. 1, Teorema 25), §5 (experimento tipo Freedman) | El holdout debe revelar lo mínimo posible; elegir variables mirando el holdout sobreajusta incluso con n = 10.000 |
| Dwork et al., *Preserving Statistical Validity in Adaptive Data Analysis*, [arXiv 1411.2664](https://arxiv.org/abs/1411.2664) | Solo el resumen | Contexto: pueden responderse muchas consultas adaptativas, en número exponencial en n |
| Roelofs, Fridovich-Keil, Miller, Shankar, Hardt, Recht y Schmidt, *A Meta-Analysis of Overfitting in Machine Learning*, NeurIPS 2019, [resumen](https://papers.nips.cc/paper/2019/hash/ee39e503b6bedf0c98c388b7e8589aca-Abstract.html) / [PDF](https://papers.nips.cc/paper_files/paper/2019/file/ee39e503b6bedf0c98c388b7e8589aca-Paper.pdf) | PDF completo: §1–2, §3.1–3.5, §5–6 | En la práctica el sobreajuste adaptativo es pequeño; el problema real son las particiones no i.i.d. y los conjuntos de prueba pequeños |
| Berg-Kirkpatrick, Burkett y Klein, *An Empirical Investigation of Statistical Significance in NLP*, EMNLP-CoNLL 2012, [PDF](https://aclanthology.org/D12-1091.pdf) | Resumen, §1–2 (bootstrap emparejado, Fig. 1) | Bootstrap emparejado; la significación está bien calibrada en i.i.d. y deja de predecir cuando cambia el dominio |
| [McNemar's test](https://en.wikipedia.org/wiki/McNemar%27s_test) y [Sign test](https://en.wikipedia.org/wiki/Sign_test), Wikipedia | Cuerpo de ambos artículos | Fórmula en notación b/c, versión binomial exacta, regla b + c < 25, independencia de los pares |
| Cameron, Gelbach y Miller, *Bootstrap-Based Improvements for Inference with Clustered Errors*, [NBER t0344](https://www.nber.org/papers/t0344) (REStat 90(3), 2008) | Solo el resumen | Ignorar la correlación dentro de los grupos produce demasiados rechazos, sobre todo con pocos grupos |

**No leídos:**
- Dietterich (1998), *Approximate Statistical Tests…*, Neural Computation 10(7): MIT Press devolvió 403, el PDF de la UGR salió ilegible por la codificación de fuentes y las otras dos copias dieron 404. No lo citamos.
- El artículo de Science 2015, *The reusable holdout*: no se descargó. Lo que decimos de Thresholdout sale de arXiv 1506.02629.

Comprobación de tus referencias: 1411.2664 es *Preserving Statistical Validity in Adaptive Data Analysis* y 1506.02629
es *Generalization in Adaptive Data Analysis and Holdout Reuse*, los dos de los mismos seis autores. Las dos referencias
son correctas.

### Lo esencial de cada fuente

- **Ladder (Fig. 1).** "If R_S(f_t) < R_{t−1} − η, assign R_t ← [R_S(f_t)]_η. Else assign R_t ← R_{t−1}." El Teorema 3.1 da un
  error de clasificación de O(log^{1/3}(kn) / n^{1/3}) tras k envíos adaptativos sobre un holdout de tamaño n.
- **Ladder sin parámetros (Fig. 2).** Usa una prueba t emparejada sobre los vectores de pérdida, t = √n · mean(u−v)/std(u−v), y
  publica solo si R_S(f_t) < R_{t−1} − s/√n. Según §4.1 eso equivale a rechazar "roughly at the 0.15 significance level". Los
  autores dicen también que su uso de la prueba "is primarily heuristic", porque f_t depende de la muestra y no se corrige por
  comparaciones múltiples. Y añaden: "A paired test has substantially more statistical power in settings where the loss
  vectors that are being compared are highly correlated as is common in a competition."
- **Ladder §5–6.** El ataque de boosting usa N = 12.000 y n = 4.000: con el mecanismo de Kaggle la estimación sale "strongly
  biased" para un vector aleatorio, mientras que el Ladder queda dentro de la desviación esperada, con un sesgo de
  O(√(log k / n)). Con datos reales (Photo Quality, 1.785 envíos) el Ladder y Kaggle ordenan casi igual. Dividir al azar
  el holdout privado muestra desviaciones "large relative to the difference in mean scores", y las 50 mejores
  soluciones están muy correlacionadas.
- **Thresholdout (Fig. 1).** Compara |E_{S_h}[φ] − E_{S_t}[φ]| con T̂ + η, donde η ~ Lap(4σ). Si no supera el umbral, devuelve
  el valor de entrenamiento y el analista solo recibe "just a single bit". Si lo supera, gasta presupuesto (B ← B − 1) y
  devuelve E_{S_h}[φ] más ruido de Laplace. El número de consultas puede ser "exponential in n as long as the number of
  times that the analyst overfits is at most quadratic in n". El Teorema 25 fija T = 3τ/4 y σ = τ/(96 ln(4m/β)).
- **Experimento de 1506.02629 §5.** Datos sin ninguna señal, con n = 10.000 y d = 10.000. Seleccionar variables
  comprobándolas en el holdout estándar da "reported accuracy of over 63% for k = 500 on both the training set and the
  holdout set", cuando la exactitud real es del 50 %. En el experimento usan T = 0,04 y τ = 0,01, valores "lower than the
  values necessary for the proof".
- **Roelofs.** Analizan 120 competiciones de Kaggle y concluyen "little to no signs of overfitting". Los casos atípicos
  tienen "non-i.i.d. data splits or (effectively) small test sets". Los efectos son "typically small (e.g., less than 1%
  classification accuracy)". Para competiciones de exactitud, "at least 10,000 examples is a reasonable minimum test set
  size". Y señalan que "distribution shifts … may be a more pressing problem than test set re-use". Los saltos en las
  curvas de p-valores los atribuyen a modelos correlacionados.
- **Berg-Kirkpatrick et al.** Bootstrap emparejado: se toman b muestras con reemplazo y se cuenta cuántas cumplen
  δ(x^(i)) > 2δ(x). Conclusiones: la significación está "remarkably well-calibrated" cuando el conjunto de prueba es i.i.d.
  respecto a los datos nuevos, pero "as the domain of the new data diverges … the predictive ability of significance level
  drops off dramatically". Además, "test size plays the largest role in determining discrimination ability, but … we get
  diminishing returns".
- **McNemar y prueba de signos (Wikipedia).** χ² = (b − c)²/(b + c), con 1 grado de libertad. La versión exacta compara b con
  Bin(b + c, ½). La aproximación χ² falla si b + c < 25. McNemar "is the same procedure as the sign test" cuando los
  resultados son 0/1. La prueba de signos supone que "the differences Zi are assumed to be independent".
- **Cameron, Gelbach y Miller (resumen).** "Standard asymptotic tests can over-reject, however, with few (5-30) clusters."

## 2. Diagnóstico: qué riesgo tenemos de verdad

1. **Holdout reutilizado.** Dev se ha usado para decidir decenas de candidatos, y cada decisión es una consulta adaptativa
   en el sentido de Dwork et al. y del Ladder.
2. **Desplazamiento de distribución.** Dev, LIVE y la ventana sellada son meses distintos. Dev ha quedado siempre por
   encima de LIVE: 77,73 frente a 77,00 en v2, 82,90 frente a 82,25 en v3 y 92,82 frente a 91,92 en v3.5. Roelofs y
   Berg-Kirkpatrick coinciden en que este es el riesgo principal, más que la reutilización del holdout.
3. **Grados de libertad del analista.** Un literal copiado de dev (una fecha, un ID, un nombre, un importe) es una
   "variable seleccionada" del experimento de Freedman. Ese experimento demuestra que n = 10.000 no protege cuando las
   opciones son muchas (d = 10.000).
4. **Métrica compuesta.** La nota oficial incluye el F1 macro de 10 clases de excepción, así que unas pocas filas de una
   clase rara mueven mucho la nota. bc030cc subió la nota en dev de 93,79 a 94,33 (+0,54) con solo 53 movimientos netos
   (+0,076 puntos de acierto). Las puertas estadísticas se calculan sobre movimientos, no sobre la nota.

## 3. Las puertas

| Puerta | Regla | Dónde se mide | Fuente principal |
|---|---|---|---|
| **G1 McNemar** | c = mal→bien, b = bien→mal; z = (c − b)/√(b + c) ≥ 3 **y** c − b ≥ 30 | `significance.py` | McNemar; Ladder sin parámetros (prueba emparejada) |
| **G2 Signos por pagador** | Sobre pagadores con saldo neto ≠ 0: binomial exacta unilateral p(P+ \| P+ + P−) ≤ 0,01, y ningún pagador aporta más del 25 % de c − b | `significance.py` | Prueba de signos (independencia); Cameron, Gelbach y Miller |
| **G3 Saldo por mes y entidad** | c − b > 0 en abril y en mayo; c − b ≥ 0 en cada entidad con más de 1.000 movimientos | `significance.py` | Roelofs y Berg-Kirkpatrick (desplazamiento); Ladder §6 (ruido de partición) |
| **G4 Ventana sellada** | Una sola ejecución por candidato aceptado; solo se lee el **signo** de Δ, que debe coincidir con el de dev | `dev.py --sealed`, a mano | Thresholdout ("a single bit"); Ladder (revelar lo mínimo) |
| **G5 Literales de fase** | Barrido del diff: ninguna fecha, ID, nombre, importe ni umbral sacado de la ventana de dev | `git diff` + grep, a mano | Experimento de Freedman en 1506.02629 §5 |
| **G6 Confirmación en LIVE** | Un CSV completo por candidato aceptado; Δ LIVE ≥ +0,01 con el mismo signo que dev | subida, `SUBMISSIONS.md` | Ladder (error del mecanismo de Kaggle ∝ √k); Roelofs |
| **G7 Variable por pase y fallo suave** | Cada pase se activa con su variable (por defecto 0); si lanza una excepción, se salta y la salida del tronco queda intacta | revisión de código | Diseño emparejado (sin fuente estadística directa) |

**Veredicto de `significance.py`:**
- **ACEPTAR** si pasan G1, G2 y G3.
- **RECHAZAR** si el candidato es significativamente peor:
  - en total (z ≤ −2), o
  - en un mes o en una entidad grande (z del tramo ≤ −2 y c − b ≤ −10), o
  - en la prueba de signos (p de que salgan tantos P− ≤ 0,01).
- **NEUTRO** en cualquier otro caso: no hay evidencia. Un candidato NEUTRO no entra en el tronco ni gasta una subida a
  LIVE. Se puede dejar en su rama con la variable a 0.

Solo un ACEPTAR pasa a G4–G6. El orden es: G7 (al escribir el código) → G5 (antes de ejecutar dev) → G1–G3 (dev) →
G4 (sellada, una vez) → G6 (LIVE, un CSV). El registro se hace en `SUBMISSIONS.md`.

### G1 — McNemar sobre movimientos discordantes

**Por qué.** Tronco y candidato se evalúan sobre los mismos movimientos, así que la comparación correcta es emparejada.
Solo informan los discordantes (b y c); los 69.000 movimientos en los que coinciden no aportan nada. Blum y Hardt
eligen la prueba emparejada precisamente porque los vectores de pérdida de candidatos parecidos están muy
correlacionados, y en nuestro caso lo están casi por completo: un pase toca unas decenas de movimientos.

**Relación con el Ladder.** Con pérdidas 0/1 la diferencia emparejada vale ±1 en los b + c discordantes y 0 en el resto.
Su desviación típica es s ≈ √((b + c)/n), y la condición R_t < R_{t−1} − s/√n se convierte en (c − b) > √(b + c), o sea
**z > 1**: el nivel de "roughly 0.15" que citan Blum y Hardt. Nosotros pedimos **z ≥ 3** (p unilateral ≈ 0,0013) porque los
propios autores reconocen que su prueba no corrige comparaciones múltiples. Con unos 40 candidatos por ventana,
0,0013 × 40 ≈ 0,05: z = 3 equivale aproximadamente a un Bonferroni al 5 % para toda la campaña.

**Por qué c − b ≥ 30.** Garantiza b + c ≥ 30, por encima del umbral de 25 por debajo del cual la aproximación normal o χ²
deja de valer. También exige un tamaño de efecto mínimo: 30 movimientos son 0,043 puntos de acierto en dev, por
debajo del ruido de cualquier otra medida que tenemos. `significance.py` imprime además la p binomial exacta.

**Dónde la teoría no sirve para nuestro tamaño.** La significación de McNemar supone que el candidato es fijo e
independiente de dev. No lo es: lo diseñamos mirando los fallos de dev. Por eso G1 es necesaria pero no suficiente, y
G4 y G6 existen para comprobar fuera de dev lo que G1 no puede garantizar.

### G2 — Prueba de signos por pagador

**Por qué.** McNemar supone que los discordantes son independientes. No lo son cuando un pase arregla un pagador: los 40
recibos del mismo cliente aciertan o fallan juntos. La prueba de signos exige "the differences Zi … independent", y la
unidad independiente más razonable es el pagador. Cameron, Gelbach y Miller muestran que ignorar los grupos lleva a
rechazar de más, y que el caso malo es tener pocos grupos (5–30). G2 cambia la unidad: cada pagador con saldo neto
distinto de cero vota +1 o −1. 30 movimientos netos de un único cliente dan P+ = 1, que no pasa, aunque G1 los cuente
como z ≈ 5. El tope del 25 % por pagador cubre el caso de muchos votos pequeños más uno grande que lo domina todo.

**Dónde la teoría no sirve.** Que haya pocos grupos depende de cuántos pagadores toca el candidato, no del tamaño de
LIVE. Con P+ + P− < 7 ni siquiera un 7–0 llega a p ≤ 0,01 (2^−7 = 0,0078 es el primer valor que lo consigue). Ese es el
precio de la prueba: un pase legítimo que solo afecta a 3 clientes grandes sale NEUTRO. Lo asumimos, porque un cambio así
es exactamente el que no se puede distinguir de memorizar a esos 3 clientes. Pagador = parte de las partidas del golden
del movimiento; si el movimiento no tiene partidas, entidad + nombre normalizado de la contraparte; si tampoco hay nombre,
el propio movimiento. Leer el golden aquí es medir, no decidir, y nunca entra en una regla.

### G3 — Saldo positivo por mes y por entidad

**Por qué.** Lo que más estropea la validez es el desplazamiento de distribución. Roelofs: los atípicos tenían "non-i.i.d.
data splits"; Berg-Kirkpatrick: la significación "drops off dramatically" cuando cambia el dominio. LIVE son otros meses y
otra mezcla de entidades. Si un candidato gana en mayo y pierde en abril, o gana en US01 y pierde en ES01, lo más
probable es que esté aprovechando una particularidad de un tramo, y en LIVE ese tramo pesará distinto. Partir dev en dos
meses es nuestra versión barata de lo que hace Blum y Hardt §6, que dividen el holdout privado en dos mitades y ven que
el ruido de partición "is large relative to the difference in mean scores".

**Umbral.** Mes: c − b > 0 en cada uno (los dos meses son grandes, unos 35.000 movimientos). Entidad: ≥ 0 solo en las
entidades con más de 1.000 movimientos. En las pequeñas un único movimiento cambia el signo y la medida es ruido; se
imprimen solo como información.

**Dónde la teoría no sirve.** Ninguna fuente da un umbral por tramo. G3 es una regla de robustez, no una prueba con
error de tipo I controlado. Es conservadora a propósito: con 18 entidades y un efecto pequeño, alguna puede quedar en −1
por azar. Ese caso cuenta como NEUTRO y no como RECHAZAR, porque RECHAZAR exige que el daño en el tramo sea significativo.

### G4 — Ventana sellada, una vez, mismo signo

**Por qué.** Thresholdout se basa en una idea: cuando una hipótesis no sobreajusta, el holdout debe devolver un solo bit, y
cada respuesta con más información gasta presupuesto. La ventana sellada (2025-01..02) es nuestro holdout de último
recurso. Para no gastarla:
- Solo se ejecuta para un candidato que ya tiene ACEPTAR en dev.
- Solo se lee el **signo** de Δ (mejora / empata / empeora). Nunca se miran segmentos ni filas de la sellada.
- Si el signo sale contrario al de dev, el candidato se rechaza y no se ajusta nada.
- Cada ejecución se apunta en `SUBMISSIONS.md`, como Thresholdout descuenta B.

**Dónde la teoría no sirve.** La garantía de Thresholdout (Teorema 25) exige
n ≳ ln(m/β)/τ² · min{B, √(B·ln(…))}. Con τ = 0,01 (un punto entero de exactitud), m = 100, β = 0,05 y B = 10, eso da del
orden de 7,6·10^5 × constantes, más que nuestra ventana sellada y más que LIVE (2,0·10^5). Además τ = 0,01 es 10 veces
más grueso que los efectos que decidimos (0,04–0,4 puntos). Hacer el umbral 10 veces más fino multiplica n por 100. El
propio artículo usa en su experimento valores "lower than the values necessary for the proof". Por eso tomamos de
Thresholdout el **principio** (leer un bit y llevar un presupuesto), no el mecanismo con ruido de Laplace, que a nuestro
tamaño no garantizaría nada útil.

### G5 — Barrido de literales de fase

**Por qué.** El experimento de 1506.02629 §5 es el caso que más se parece a nuestro riesgo. Con n = 10.000 y etiquetas
aleatorias, quedarse con las variables que también se ven correladas en el holdout da más del 63 % "on both the training
set and the holdout set", con una exactitud real del 50 %. Cada literal sacado de dev (una fecha como 2025-04-xx, un
BT-/ARI-/ARC-/API-/APC-/CUS-…, un nombre de cliente, un importe exacto, un umbral ajustado a un caso) es una de esas variables
seleccionadas, y el holdout no avisa porque es el mismo sitio de donde salió la variable.

**Procedimiento.** Sobre `git diff tronco..candidato -- src solution`:
- `grep -nE '20(24|25)-[0-9]{2}|BT-|ARI-|ARC-|API-|APC-|CUS[0-9]|SUP[0-9]|[0-9]+\.[0-9]{2}\b'` más una lectura de cada
  constante nueva.
- Cada literal tiene que justificarse con POLICIES.md, DATA_DICTIONARY.md o una regla genérica. "Así sale en dev" no
  vale.
- Los umbrales numéricos se fijan en TRAIN fuera de la ventana, o se redondean a valores de política (1 %, 10 %, 5 días).

**Dónde la teoría no sirve.** No hay ningún resultado que convierta "número de literales" en un error controlado: G5 es
revisión, no estadística. Es la puerta más barata y la que tapa el modo de fallo que G1–G3 no detectan, porque un literal
memorizado da un McNemar excelente.

### G6 — Confirmación en LIVE, un CSV entero por candidato

**Por qué.** El marcador de LIVE es un holdout de estilo Kaggle. Blum y Hardt demuestran que con ese mecanismo el error
del marcador crece "as √k" con el número de envíos, y que el ataque de boosting saca una nota sesgada sin ninguna mejora
real. La defensa es que **LIVE no decide**: dev decide y LIVE solo confirma.
- Un CSV completo por candidato aceptado.
- Nunca se suben variantes para ver cuál sale mejor.
- Nunca se ajusta un parámetro mirando LIVE.

Así k queda en el número de candidatos aceptados (unas decenas en todo el proyecto) y no en el número de ideas.

**Regla.** Δ LIVE ≥ +0,01, la unidad que muestra el marcador, y del mismo signo que en dev. Si Δ = 0,00 el resultado es
NEUTRO y se queda en el tronco solo si G1–G4 eran claros. Si Δ < 0, se revierte.

**Dónde la teoría no sirve, y por qué no hace falta aquí.** La cota del Ladder, O(log^{1/3}(kn)/n^{1/3}), con n = 203.083
y k = 50 da log^{1/3}(1,0·10^7)/58,8 ≈ 2,53/58,8 ≈ **0,043**, unos 4 puntos de exactitud. La cota inferior de peor caso,
√(log k / n) = √(3,9/203.083) ≈ **0,0044**, sigue siendo 0,44 puntos. Las dos son de 10 a 100 veces mayores que los
efectos que medimos, así que como garantía no informan de nada. Lo que sí informa a nuestro tamaño es el **ruido
emparejado**:
- Un candidato que en dev cambia unos 60 movimientos cambiará unos 175 en LIVE (por la proporción 203/69,5).
- Con b + c ≈ 175, la desviación del saldo neto es ≈ √175 ≈ 13 movimientos, es decir, 0,006 puntos de acierto.
- Una ganancia real de +0,08 sobresale con claridad.

Roelofs encuentra en la práctica efectos menores de un 1 % y recomienda al menos 10.000 ejemplos; nosotros tenemos 20
veces más. Pero su resolución (alrededor de 1 punto) es más gruesa que nuestras decisiones (alrededor de 0,1), así que
su tranquilidad no se puede trasladar sin más. La brecha dev → LIVE de unos 0,7–0,9 puntos que vemos con cada versión
muestra que hay desplazamiento, y G6 lo mide candidato a candidato.

### G7 — Variable de entorno por pase y fallo suave

**Por qué.**
1. **Diseño emparejado limpio.** G1 y G2 solo valen si tronco y candidato difieren únicamente en el pase. Con
   `PASE=0`/`1` sobre el mismo commit y los mismos datos, la diferencia es exactamente esa, que es el supuesto de
   McNemar y de la prueba t emparejada del Ladder.
2. **Revertir sin tocar código.** Si G4 o G6 fallan, basta con poner la variable a 0. No hay que hacer un revert que
   pueda arrastrar otros cambios.
3. **Fallo suave.** Si el pase lanza una excepción, se registra, se salta y la salida del tronco queda intacta. Un
   candidato nunca empeora el CSV por romperse en LIVE o en FINAL con datos que dev no tenía.

Ejemplo: `PAYER_ID2` en `model/v6d-payer`, con valor por defecto 0.

**Fuente.** Ninguna de las leídas trata esto directamente. Es ingeniería al servicio del supuesto emparejado de G1 y G2,
y conviene decirlo así en lugar de inventarle una cita.

## 4. Lo que el método no garantiza

- **Nada de esto es una cota de peor caso.** Las garantías formales (Ladder, Thresholdout) existen, pero a n = 203.083 y
  con efectos de 0,1 puntos no dicen nada útil (G4, G6). El método es empírico y se apoya en Roelofs: en la práctica
  el sobreajuste adaptativo es pequeño, y el peligro real está en el desplazamiento y en la libertad del analista.
- **G1–G3 miden acierto por movimiento, no la nota compuesta.** Un candidato puede pasar G1 y mover el F1 macro de
  excepciones más o menos de lo esperado. G6 es la que confirma la nota.
- **Dev sigue siendo un holdout reutilizado.** Si con el tiempo la brecha dev − LIVE crece (hoy 0,7–0,9), dev se está
  gastando. Entonces hay que rotar la ventana de dev (`dev.py --start/--end`), no relajar las puertas.

## 5. Uso

```
python solution/tools/significance.py dev_runs/<tronco>/sub.csv.gz dev_runs/<candidato>/sub.csv.gz \
    --data ~/hackdata/data_dev [--json notes/sig_<commit>.json]
# código de salida 0 = ACEPTAR, 1 = NEUTRO, 2 = RECHAZAR
```

Solo lee archivos: no ejecuta el pipeline. Tarda unos 2,5 s y ocupa poca memoria. Las dos salidas tienen que ser de la
misma ventana y el mismo dataset.

**Prueba de referencia (bc030cc, exc_overpay, frente a su base):**

`~/Downloads/talky-exc/dev_runs/{base,overpay}`, con `--data ~/hackdata/data_dev_exc`:

| | Resultado |
|---|---|
| G1 | c = 55, b = 2, c − b = +53, z = 7,02, p exacta 1,1e-14 → pasa |
| G2 | 57 pagadores movidos, P+ = 55, P− = 2, p = 1,1e-14; el mayor pagador aporta el 2 % → pasa |
| G3 | abril +25, mayo +28; ninguna entidad > 1.000 en negativo (DE01 queda en 0) → pasa |
| Veredicto | **ACEPTAR** |

Comprobaciones de sentido: con el orden invertido sale RECHAZAR (z = −7,02) y una salida contra sí misma sale NEUTRO.
La base de esa prueba es el run `base`, hecho con 7506fc5. Entre 7506fc5 y 2b833dc (el padre de bc030cc) solo cambian
cascade.conf, run_final.sh, la carga de la cascada en dev.py (7 líneas) y notas; el resultado lo confirma (55/2).
