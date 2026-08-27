# Cotas aceptadas y decisiones deliberadas

> **Para qué existe.** Nueve ciclos de auditoría (G1–G8 + C9) y una fase de optimización
> dejaron ~150 limitaciones **aceptadas a propósito**, cada una con su motivo medido,
> repartidas entre docstrings, comentarios, mensajes de commit y nueve informes de
> 20–70 KB. Este fichero es el índice: **qué está acotado, por qué, y dónde vive la
> versión larga.** No sustituye al código — lo apunta.
>
> **Regla de lectura.** Una cota que aparece aquí es una decisión, no un descuido: antes
> de "arreglarla", lee su motivo. Varias se intentaron cerrar y el intento fue peor que la
> cota (están marcadas **VÍA MUERTA**).
>
> **Estado de verificación.** Marco `[V]` lo que he comprobado ejecutando o leyendo el
> código al escribir este fichero (2026-08-27), y `[I]` lo que viene de un informe y **no**
> he vuelto a medir. Ninguna cifra `[I]` debe citarse como hecho sin re-medirla.
>
> **Pasada del 2026-08-27 por la tarde.** A los nueve ciclos internos se sumó una
> **auditoría externa independiente** (veredicto NO-GO con cinco condiciones) que encontró
> cuatro hallazgos, ya corregidos, y dos hipótesis más que resultaron ciertas. Además se
> ejecutaron las dos maniobras que §9 listaba como pendientes. **§9 está reescrita entera**;
> §9.1 cuenta el caso en que un arreglo mío cerró un falso rojo y abrió el falso verde
> opuesto — el ejemplo canónico de §0.

---

## 0. La regla de oro del proyecto

**Un documento puede afirmar por escrito una garantía que el código no da.** Es el hallazgo
transversal de los nueve ciclos, y se formuló dos veces:

- G5: «el commit que los introduce **declara por escrito una garantía que su código no da**».
- G6: «el código hace algo distinto de lo que **su commit, su docstring o su test de
  regresión** afirman».

Dos corolarios que los ciclos convirtieron en método:

1. **Toda guarda que fije una cota tiene que afirmar el NÚMERO literal**, y probarse por
   **mutación** (degradar la constante al valor previo y exigir rojo). Un test que lee la
   cota del módulo que audita (`getattr(mod, "_COTA", 5)`) no mide nada.
2. **Toda guarda de una propiedad de carrera se escribe con dos sesiones entrelazadas y
   ≥2 filas**, y toda guarda de un contrato cliente-servidor tiene que ejercitar el camino
   que el cliente real recorre — si nadie lo recorre, **eso es el hallazgo**.

---

## 1. Parser de salarios — `backend/services/data_normalizer.py`

Rompió **cuatro veces por el mismo sitio** (la regla de desempate). Las cuatro versiones se
validaron contra el mismo material: los **637 valores** de `jobs.salary_original`, todo
ASCII generado por máquina. Ese corpus **no puede refutar nada**: no contiene ni una
referencia, ni un año, ni una escala salarial, ni un `bis`. De ahí
`backend/tests/test_g8_corpus_prosa_salarios.py`, con 62 anuncios de prosa realista.

### La regla única vigente `[V]`

Compiten tres patrones y **gana el primero del texto**. `plain` (el que no lleva divisa, y
por tanto el único que puede casar ruido puro), si va primero **y hay otro candidato**,
debe superar **las dos** pruebas: MAGNITUD (`_low_looks_like_salary`) **Y** ANCLA LÉXICA
(abrir el texto, o ir tras una palabra de sueldo dentro de `_ANCLA_VENTANA` = 28 caracteres).

### Las cuatro cotas `xfail(strict=True)` `[V]`

Estrictas **a propósito**: un `pytest.xfail()` imperativo cortocircuita el test y jamás
avisaría de un XPASS, que es justamente la señal que se quiere.

| id | Cota (literal del código) |
|---|---|
| **A7** | «cota aparcada G5/P3-5: sin rango que casar, `single` es leftmost y se queda con el numero de escala de dos digitos (12)» |
| **A8** | «la escala mete el importe entre parentesis y `to` une los DOS parentesis, no los dos importes» |
| **D10** | «cota declarada del ancla lexica (G8/P2-1): un `plain` en medio de la prosa y sin palabra de sueldo delante pierde ante un candidato con divisa, aunque ese candidato sea una glosa entre parentesis» |
| **J3** | «el rango va invertido por pensum y el parentesis de porcentaje corta el patron; `normalize_salary` haria el swap, pero no llega» |

**D10 es la única que introduce el fix de G8**; las otras tres son preexistentes.

### Otras cotas del parser

| Cota | Motivo |
|---|---|
| «y hay otro candidato» no es cosmética `[V]` | Exigirle magnitud+ancla a `plain` **siempre** rompe tres filas reales: `12-42508 EUR`, `21-42508 EUR`, `720-2400 EUR` |
| **VÍA MUERTA** `[V]` | «un candidato con divisa y FUERA de paréntesis gana a `plain`» recupera las nueve formas y es neutra sobre el corpus vivo, **pero rompe un caso ya fijado en la suite** (`Salaire annuel 90 000 - 110 000, soit 7 500 - CHF 9 200 par mois` → `(7500, 9200)`): la glosa no siempre va entre paréntesis. **No repetirla.** |
| Moneda desconocida ⇒ importe descartado `[I]` | Mejor sin salario que un salario ×100: los factores reales medidos eran ZAR ≈ ×21, INR ≈ ×106, y había **107 filas ya corruptas** en `jobgether` |
| `_MAX_SALARY_TEXT = 200` `[V]` | Es la cota de la columna `String(200)`, y contiene un O(n²) medido en **37 s** con `'9'*16000` |
| `_MAX_CHF_INTEGER = 2**31 - 1` `[I]` | `salary_*_chf` son INT4 y el multiplicador de periodo puede desbordar partiendo de un valor que sí cabía |

> **Regla general para TODO scraper** (y la más cara de olvidar): deja `salary_*_chf = None`
> y pasa `salary_original` + `salary_currency` + `salary_period`. `DataNormalizer` convierte.
> **Prellenar los `_chf` con una divisa que no sea CHF los guarda sin convertir** —
> `normalize_salary` tiene early-return si ambos `_chf` están puestos — y eso es corrupción
> de ~2000× en €/hora.

---

## 2. Deduplicación

### 2.1 La cota estructural que condiciona todo lo demás `[I]`

`paraphrase-multilingual-MiniLM-L12-v2` tiene **`max_seq_length = 128` tokens** (medido en
vivo) y `build_job_text` no trunca ⇒ el encoder ve el arranque de la descripción, que suele
ser **boilerplate del empleador**. Con umbral 0,95 el coseno deja de medir identidad y mide
**cuánto boilerplate comparten**:

```
boilerplate x0 (  0 chars) -> coseno=0.4502
boilerplate x2 (344 chars) -> coseno=0.9757  DUPLICADO
boilerplate x4 (688 chars) -> coseno=1.0000  vectores IDENTICOS
```

…entre «Sachbearbeiter Finanzbuchhaltung» y «Gärtner Grünflächenunterhalt». **Subir el
umbral no sirve.** El vector está persistido en pgvector y lo consumen matching **y** dedup:
cambiar el texto embebido exigiría **re-embeber el corpus entero**.

### 2.2 Cross-idioma: RETIRADO `[V]`

La excepción que se saltaba la puerta léxica cuando los idiomas diferían se **retiró entera**
(`0465681`). Tres motivos medidos con el encoder real:

1. **La separación estaba invertida**: una maestra de primaria (DE) y un contable (FR) del
   mismo municipio puntúan **0,8220**, por encima del duplicado real (**0,8195**). No existe
   umbral que recoja el segundo sin el primero.
2. **No servía de nada**: a umbral 0,95 el prefiltro SQL mata el par una capa antes. Sobre
   200 activas, los pares cross-idioma son **0 a los cuatro umbrales** probados.
3. **El precio de equivocarse no es cosmético**: `mark_duplicate` escribe `duplicate_of`
   **y `is_active=False`**. Este proyecto ya perdió **664 vacantes reales** así.

**Cota aceptada:** la misma vacante publicada en dos idiomas **no** se deduplica por esta
vía. Lo cubre `fuzzy_hash` cuando el título coincide. Es el lado seguro: un duplicado que
sobrevive se ve en el catálogo; una vacante real desactivada, no.

**Si alguna vez hace falta reabrirlo, NO basta con bajar el umbral.** El discriminante que
sí cruza idiomas es el **coseno de los títulos solos** (sin boilerplate): medido sobre 8
pares reales DE/FR y 8 falsos del mismo municipio, separa en el sentido correcto —
`min(reales)=0,6067 > max(falsos)=0,5033` — al contrario que el coseno del texto completo.
Haría falta ese discriminante **Y** su propio umbral **Y** abrir el prefiltro; y por (2) hoy
no tendría ningún par sobre el que actuar.

> ⚠ **El docstring del método sigue diciendo lo contrario.** Ver §8.

### 2.3 Otras cotas de dedup

| Cota | Motivo |
|---|---|
| Exclusión `Job.source != source` `[V]` | Deliberada; los reposts intra-fuente los cubre la identidad exacta (`hash` / índice único de `url`) |
| `_SEMANTIC_CANDIDATE_LIMIT` por antigüedad `[I]` | Puede dejar fuera al duplicado real; falso negativo aceptado |
| Caída al barrido exacto por saturación HNSW `[V]` | Si **todas** las filas devueltas por el índice caen dentro del radio, el conjunto puede estar truncado y el canónico podría cambiar. Se compara contra **lo que el índice devolvió de verdad**, no contra el tope pedido: `hnsw.ef_search` (40) manda sobre el `LIMIT` |
| Falso positivo entre vacantes del mismo empleador `[V]` | La puerta es LÉXICA: dos títulos que comparten un tercio del léxico pueden casar si además comparten cantón y no declaran salario |
| `find_same_source_clone` NO escribe nada `[I]` | Solo `select`; el llamante cuenta y loguea. «La lección de las 664 vacantes recuperadas está respetada» |
| `fuzzy_hash` sin versión ni backfill automático `[I]` | Cambiar la fórmula obliga a ejecutar el script de backfill a mano; nada lo hace cumplir |
| El superviviente de la canonización conserva SU contenido `[I]` | En 254 de 305 grupos el clon es más reciente; se autocura en la primera cosecha posterior |
| Reemisión con id nuevo NO es canonizable `[I]` | Medido en las 17 fuentes con grupos gemelos: la decisión de no canonizar más fuentes está medida, no supuesta |

> **La maniobra de canonización ya NO es hipotética: se ejecutó el 2026-08-27** (commit
> `2462717`). Las dos cotas de arriba describen ahora el estado real de la base, no un plan.
> Cifras y verificaciones posteriores en **§9**.

---

## 3. Matching, LLM y embeddings

| Cota | Motivo |
|---|---|
| **Sin `LIMIT` en la etapa 1** `[V]` | «todas las ofertas activas, sin tope» es deliberado: es lo que permite que el **umbral** decida en la etapa 2, en vez de un top-K fijo |
| Modo avalancha: `>MATCH_LLM_RERANK_MAX ⇒ top 50` `[I]` | Coste de IA acotado. **Pero no es recuperable**: el `head` se elige por el `score_final` de la etapa 2, calculado con `llm_score=0.0`, así que una oferta bajo el puesto 50 tiende a quedarse ahí |
| Un lote degradado JAMÁS se cachea `[I]` | Verificado con Redis real: tras forzar degradación, 0 claves. Evita que un fallo del LLM se sirva 7 días |
| Tamaño de lote de rerank = 10 `[I]` | 3.728 tokens × `GROQ_CONCURRENCY=2` = **93 % del límite de 8k/min** del tramo gratuito. Subirlo a 20 lo reventaría. «La palanca es la caché, no el lote» |
| Alerta de profesor **sobre-inclusiva a propósito** `[I]` | «esta alerta es opt-in y su razón de ser es NO perder la vacante» |

**Pesos reales del matcher `[V]`** — son **SEIS** factores, no cinco:

```
embedding 0.35 · salary 0.15 · location 0.10 · recency 0.15 · llm 0.15 · language 0.10
```

**Umbral efectivo `[V]`**: `MATCH_SCORE_THRESHOLD` vale **42.0** (`.env`), no el 35.0 del
default de `config.py`. Al citarlo, cita el `.env`.

---

## 4. Crawler y scraping

| Cota | Motivo |
|---|---|
| **El incremental gatea LISTADOS, no DETALLES** `[I]` | `_collect_page_jobs` baja el detalle de todos los stubs **antes** de evaluar `_page_all_known`. Con `RATE_LIMIT_SECONDS=2.0` y `PAGE_SIZE=20`, una página íntegramente conocida cuesta **20 peticiones + 40 s diarios para no aprender nada**. Afecta a `schuljobs`, `myscience`, `gastrojob`. Aparcado con evidencia — y **contradice la premisa declarada** del cursor incremental |
| **Los 15 scrapers corren EN SERIE** `[I]` | Paralelizarlos bajaría 321 s → ~140 s (2,3×), pero **15 conexiones simultáneas desde una IP son una huella distinta de 15 secuenciales**. Es una llamada de criterio del dueño, no una optimización que un auditor deba dar por buena. **No aplicado** |
| Degradación parcial = `ok` `[I]` | Política declarada. `adzuna` mete tres sub-fuentes (DE/AT/GB) bajo un `source_key`: sale `ok` si una de las tres trae ofertas |
| Prioridad `error > None > known_page` `[I]` | `_stop_reason` solo toma esos tres valores en todo el backend (verificado con `grep` sobre los 5 ficheros que lo asignan), así que no puede colapsar un cuarto estado |
| `jobicy.py` NO se borra pese a estar desactivado `[I]` | Tiene 21 menciones en 5 ficheros de test como fixture genérica de «un provider cualquiera» |
| `restricted.py` — gating doble real `[V]` | Sin credencial de partner **no se instancian**: 0 peticiones, nunca scraping público. Verificado en G1, G3 y G5 |
| `Retry-After` se ignora `[I]` | Siempre backoff exponencial propio. «Sin medir → no lo priorizo» |

### El bug de autolimitación del presupuesto (cerrado, pero léelo antes de tocarlo) `[I]`

El presupuesto se derivaba de una media móvil **cuyo insumo estaba capado por el propio
presupuesto**: la EMA nunca podía aprender una demanda mayor que el techo que ella misma
fijaba. Con cosecha *newest-first*, lo que quedaba bajo el horizonte se hundía y **no se
recuperaba jamás** — y nada lo contaba: ni `window_skipped` (nunca se descargó) ni error
(el run fue «correcto»). Pérdida silenciosa.

El arreglo usa la señal que ya existía (`_stop_reason`): si el run agotó el presupuesto
**SIN** early-stop, terminó «con hambre» y la pasada siguiente **reabre el bootstrap**.
Caso agudo: `tes`, donde el servidor impone **una oferta por página**, así que el margen de
seguridad de «una página» era **UNA OFERTA**.

---

## 5. Redacción de secretos — `backend/utils/redact.py`

Vive en **un solo sitio** y se aplica en las **dos raíces** —el filtro de logging sobre los
handlers del root, y `fetch_diagnostics.record()`—, no parcheando cada llamada: «alguien
tiene que acordarse» es exactamente la fragilidad que se está cerrando.

| Cota | Motivo |
|---|---|
| **La credencial en el PATH sin nombre NO se cubre** `[V]` | jooble (`/api/<clave>`) es indistinguible de un segmento de ruta cualquiera. Se tapa **en origen**, con el `diag_url` que el provider ya pasa |
| **Falso positivo conocido y aceptado** `[V]` | Barrido sobre 18.025 líneas del journal vivo: **36 líneas cambian y 31 son el banner de arranque de Celery** (`key=ai` es una *routing key*, no una credencial). Se acepta porque no pierde información — la misma línea sigue mostrando el nombre de la cola dos veces. Un umbral de longitud lo taparía **y dejaría fuera las credenciales cortas**: no se pone |
| **Percent-encoding: fuga residual** `[I]` | La guarda `if valor.startswith(("%", "{"))` —que existe para no romper el `msg % args` de una plantilla sin formatear— **no puede distinguir `%s` de `%2F`**. Un secreto que empiece por `+` o `/` percent-codificado (`%2B…`, `%2F…`) sale entero. Alcanza a `signature`/`sig`/`hmac`, que están en la lista **precisamente por ser base64**: ~3 % de los secretos base64, y es fuga **TOTAL, no parcial** |
| El filtro puede reventar a su llamante `[I]` | Llama a `record.getMessage()` dentro del filtro, y las excepciones de un `Filter` no las captura `Handler.handle`. Invierte el contrato «logging nunca revienta a su llamante». Prevalencia medida: **0** |
| `jobhunt_core/` **no tiene redacción alguna** `[I]` | Su autenticación va por cabecera (`HTTPBearer`), no por query string |

---

## 6. Sombra, gate y entrega (core)

| Cota | Motivo |
|---|---|
| **Gracia del gate = EVIDENCIA POSITIVA** `[V]` | La ausencia de evidencia **nunca** concede la gracia (*ausencia de evidencia ≠ evidencia de ausencia*). Costó **cuatro reinterpretaciones**, cada una con su falso VERDE o falso ROJO reproducido; la última la fabricaba sola la purga de retención. El docstring lo dice en su encabezado: «lleva cuatro ciclos reinterpretándose» |
| `perdida == 0` y `outbox_dead == 0`, estrictos `[I]` | Un solo dead pinta el ciclo en rojo y resetea la racha de 7. Deliberado |
| **El deadlock entre sentencias EXISTE y se decide NO tocarlo** `[I]` | Se reprodujo, «su dirección de fallo es benigna y su frecuencia medida es cero» (0 en 600 ciclos). Solo se unifica el orden de lock **dentro** de cada sentencia, que es gratis. Razón escrita: «forzar el orden dentro de `retire_poisoned` es lo que rompió esta zona en G5, G6 y G7» |
| El modelo de locks descansa sobre una invariante **del llamador** `[I]` | «un solo mark por iteración». Ningún sitio del módulo la enuncia. Se acepta hoy; Fase C la rompería |
| `lease_overrun` puede contar una fila dos veces `[I]` | «es un contador de alarma, no una métrica de gate» |
| El espejo del criterio de la gracia es exacto **fila a fila**, aproximado **entre filas del mismo lote** `[I]` | Precondición inalcanzable con el esquema legacy actual: `is_active` está presente en 5.972/5.972 filas |
| `job_ref` identifica al **SLOT, no a la vacante** `[I]` | Medido: 569 `source_listings` `legacy:*` con más de una vacante. Riesgo **DIFERIDO**: el fix real (ref por `vacancy_id`) es otro trabajo |
| El código de cutover de un solo uso **NO se refactoriza** `[I]` | `_classify_expected` (CC 66) y `verify_migration` (CC 41): «con la maniobra de migración pendiente, tocarlos ahora es exactamente lo que no hay que hacer. Anotar y dejar» |
| El `commit` por entrega del dispatcher se conserva `[I]` | ~13 s/día. «Es un intercambio deliberado que compra corrección» y toca un camino crítico recién estabilizado tras ocho ciclos |
| Los INSERT por par del dedup core siguen uno a uno `[I]` | 0,064 ms de ida y vuelta, 2.549 candidatos/mes; y `rowcount` de un `executemany` **no es fiable** para el contador que el gate lee |

### `ensayo_c2`: el único ejemplar del corpus del holdout de agosto `[V]`

Descubierto por la auditoría G9 (P3-E) y **verificado aquí con `SELECT`**: la base
`ensayo_c2` del clúster local (169 MB, 27.723 vacantes) es la **única** copia del corpus
sobre el que se muestreó y etiquetó el holdout `holdout-dedup-2026-08-23`. Los 115
`vacancy_id` del `holdout_map.csv` están **todos** allí y **ninguno** en el corpus vivo:
el holdout nunca se midió sobre este corpus, y por eso no hay nada que restaurar —hay que
volver a muestrear (protocolo intacto, semilla nueva, excluyendo los pares que ya viven en
`labeled_dedup_pairs`, incluidos los 187 de `positive-stratum-v1`).

- **No estaba en ningún inventario** y convive con tres bases de ensayo desechables
  (`jobhunt_cap_*`, `jobhunt_rehearsal_pf_*`, `swissjobhunter_migration_smoke`): cualquier
  limpieza rutinaria la habría borrado sin que nadie lo notara. Queda declarada aquí.
- **Respaldada fuera del clúster por el propietario el 2026-08-27.** No la borres ni la
  reutilices como base de ensayo: es un **archivo histórico**, no un desecho.
- El resultado del holdout (precision 0.636 / recall 0.259) se conserva como medición
  histórica **sobre otro corpus**; la cohorte se declara retirada.

### `claims` y la retirada por veneno `[V]`

- `attempts` = transportes **EJECUTADOS**; se consume en el **resultado**, nunca en el
  claim (garantía: *no gastar intentos sin transporte*).
- Eso abría un agujero: un payload que **mata al proceso** del dispatcher nunca marca, no
  consume `attempts`, el dead-letter por agotamiento **jamás llega**, y como el claim ordena
  por `next_attempt_at NULLS FIRST` ocupa la **cabeza de la cola**.
- `claims` = **reclamos consecutivos SIN resultado**, a 0 en cuanto la entrega produce uno.
- **Por qué 25**: con el beat cada 5 min son ~2 h de crash-loop ininterrumpido sobre el
  MISMO mensaje, y como **triplica** `MAX_ATTEMPTS` (8), un destino simplemente caído
  siempre muere antes por la vía normal.
- Backfill `claims = attempts`: cota **inferior honesta** que jamás mete una fila sana en el
  umbral de veneno.

---

## 7. Tests e infraestructura

| Cota | Motivo |
|---|---|
| `EXISTS` en la misma transacción, **y no `pg_stat_user_tables`** `[V]` | Las estadísticas del recolector van con retraso y una tabla sucia que llegara tarde contaminaría el test siguiente |
| El esquema a ámbito de sesión no pierde fidelidad `[V]` | `create_all` y los DDL son idempotentes: «si estuviera mal, estaría mal también la primera vez» |
| `tests_live/` **fuera de `testpaths`** `[V]` | Único autorizado a gastar dinero y depender de la red. Comprueba contra el proveedor real las mismas propiedades de forma que finge el doble, **y quien juzga es el MISMO parser que usa producción**: es lo que impide que el doble y la realidad diverjan en silencio |
| El doble de LLM sustituye **solo la llamada saliente** `[V]` | Prompt, parseo, saneo, batching, caché y elección de fallback siguen siendo código real y **siguen pudiendo fallar**. Mockear aquí **aumenta** lo que la suite puede refutar |
| `NullPool` se CONSERVA en la suite `[I]` | El pool normal ahorraría ~150 s más **pero rompe 85 de 85 tests** (`pytest-asyncio` en modo `auto` crea un bucle por test), y hay tests que dependen del comportamiento del pool. «Se aborda después, nunca a la vez» |
| ⚠ **Dos `pytest` concurrentes se pisan** `[I]` | El teardown hace `TRUNCATE ... CASCADE` de todas las tablas de `swissjobhunter_test`: dos corridas simultáneas = deadlocks y falsos rojos. Un rojo de 8F+2E se cerró como artefacto de concurrencia |
| Cada test del core sigue creando **su** BD desechable y migrándola desde cero `[V]` | La optimización de `run_alembic` quita el `fork` y el `import`, no la fidelidad. No se comparte base, ni esquema, ni estado de Alembic |
| ⚠ **Los tests del core exigen el override de desarrollo** `[V]` | Desde `ae7fbf2` el compose base **no monta** `./jobhunt_core`. `docker compose run --rm core-migrate python -m pytest …` **a secas prueba el código de la IMAGEN**, no el árbol de trabajo, y puede salir verde sobre código que no es el que acabas de editar. El comando correcto lleva `-f docker-compose.yml -f docker-compose.dev.yml` |

**Contadores vigentes al 2026-08-27** (los aporta el propietario, no re-medidos en esta
pasada — `[I]`): core **675**, backend legacy **2.280** (+4 xfailed), backend de portfolio
**1.823** (+1 skip), frontend **330**.

**Protocolo de sondas** que las auditorías siguieron y conviene mantener: escrituras **solo**
contra `swissjobhunter_test`, en transacción revertida; contra producción **solo `SELECT` y
`EXPLAIN (ANALYZE, BUFFERS)` sobre `SELECT`**; claves Redis con prefijo, borradas al
terminar; **ningún servicio reiniciado, recreado ni reconstruido**; árbol del repo intacto.

> **Trampa de instrumento, medida:** el muestreo de `pg_stat_*` debe hacerse **desde
> conexiones distintas**. Dentro de una misma transacción, `stats_fetch_consistency = cache`
> sirve una instantánea cacheada — la primera medición de la auditoría de optimización dio
> «0 escrituras» por eso.

---

## 8. Afirmaciones falsas — RE-VERIFICADAS el 2026-08-27 por la tarde

> ⚠ **Esta sección se titulaba «Afirmaciones falsas VIVAS (no corregidas)» y era ella misma
> una afirmación falsa.** Al comprobar sus seis entradas **una a una contra el código**,
> **cinco ya estaban corregidas** — dos de ellas por `de56ae0`, minutos después de que se
> escribiera esta sección; las otras tres el 2026-08-26, o sea **antes**. El registro de
> cotas se convirtió en el ejemplo de §0 aplicado a sí mismo. Es exactamente por eso que
> una entrada `[V]` tiene que re-verificarse cada vez que se cita, no heredarse.

### Cerradas — comprobadas hoy en el código `[V]`

| Dónde | Qué afirmaba | Comprobación de hoy |
|---|---|---|
| `backend/services/deduplicator.py`, docstring de `find_semantic_duplicates` | «la puerta se SALTA cuando los idiomas declarados difieren» | ✅ **Corregido** (`de56ae0`). El docstring habla ya en pasado de «la excepción que **saltaba** la puerta léxica», y el comentario del cuerpo explica que `a63745c` la retiró |
| `backend/services/scheduler.py`, log de resumen | «URL check weekly Sun 03:00» | ✅ **Corregido** (`de56ae0`). `grep -n "weekly\|Sun" backend/services/scheduler.py` no devuelve **ni una línea** |
| `backend/scrapers/irishjobs.py`, docstring | Los dos hosts «se deduplican por ese `id` de plataforma» | ✅ **Rectificado** el 2026-08-26 (`319fbe7`). El docstring dice ahora que los ids compartidos entre hosts son **0**, que las descripciones idénticas tienen ids distintos y que `_dedupe_new` **nunca** ha deduplicado cross-host |
| `backend/services/groq_service.py` | «Este es el único borde por el que entra la respuesta del LLM» | ✅ **Rectificado** el 2026-08-26 (`c056837`). El comentario lo declara FALSO y explica el segundo borde (la caché); `_get_cached` re-sanea lo que lee y la clave lleva versión de esquema |
| `jobhunt_core/delivery.py`, pre-SELECT de `mark_delivered` | «las filas que va a tocar el UPDATE son **exactamente éstas**» | ✅ **Rectificado** el 2026-08-26 (`4fe2378`). La frase ya no está en el fichero |

### Abierta — y no se puede cerrar

| Dónde | Qué afirma | Qué es en realidad |
|---|---|---|
| **`fbe22f0`**, mensaje del commit de consolidación `[V]` | «1.083 escrituras/s, 42 GB de WAL al día» | Medido con **dos instrumentos independientes que coinciden dentro del 0,6 %**: **559–979 UPDATE/s** (48,3 M/día) y **228 GB/día**. **Un mensaje de commit es inmutable**: esta entrada no se cierra nunca, solo se anota. Las cifras válidas son las del informe de optimización. ⚠ `PROMPT_AGENTE_EXTERNO_2026-08-27.md` propagó la cifra refutada |

---

## 9. Estado operativo — lo que está pendiente y NO es código

> **Actualizado el 2026-08-27 por la tarde.** Las dos maniobras que esta tabla listaba
> como pendientes **se ejecutaron esa misma mañana**. Lo que sigue abierto está abajo,
> en «Sigue pendiente».

### Ya hecho el 2026-08-27

| Qué | Estado verificado |
|---|---|
| **Migración legacy `b3c7d1a95e42`** | ✅ **APLICADA** `[V]`. `SELECT version_num FROM alembic_version` → `b3c7d1a95e42`, y los tres defaults leídos de `information_schema.columns` **en la base real** son `clock_timestamp()`: `jobs.first_seen_at`, `jobs.last_seen_at`, `match_results.created_at`. 37 regresiones de marcas de agua en verde. Copia previa en `/home/lothar/Documents/swissjob_pre_b3c7d1a95e42_20260827.sql.gz` |
| **Scripts de canonización de identidad** | ✅ **EJECUTADOS** contra `swissjobhunter` (local), commit `2462717`, autorizados por el propietario `[V]`. Ensayo en seco y ejecución dieron cifras **idénticas**. g3 (arbeitnow + jobgether): 5.419 identidades reescritas, 406 clones fusionados, 30 `match_results` descartados y **0 con señal del usuario**, 5.263 slots de sombra reapuntados + 371 de clones. g6 (irishjobs): 879 reescritas, 40 clones, 0 descartes, 879 slots reapuntados + 40 de clones. Copia previa en `/home/lothar/Documents/swissjob_pre_canonizacion_20260827.sql.gz` (117 MB, incluye el esquema `jobhunt`). **Los ficheros del repo siguen terminando en `ROLLBACK`: seguros por defecto** |
| **La otra mitad: `shadow/canonical_refs.py`** | ✅ **EJECUTADA** en la misma parada `[V]`. 6.298 filas canonizadas en el mapa; 10 juicios y 162 pares re-mapeados |
| **Comprobación posterior — huérfanos** | ✅ `[V]` re-medida hoy con la consulta literal del PASO 7 del script: **1.335** slots huérfanos en las tres fuentes canonizadas (arbeitnow 1.274 + jobgether 21 + irishjobs 40) = los **924** previos + 371 + 40, **exactamente lo previsto**. NO los 7.477 que habrían delatado el fallo que el PASO 7c evita |
| **Comprobación posterior — etiquetas** | ✅ `[V]` re-medida hoy: **91 de 91** juicios siguen resolviendo contra `source_listings` de fuentes `legacy:%` → **cero etiquetas perdidas**. De los 779 pares de `seed_duplicate_of`, **261** tienen sus DOS refs resueltos (la medición previa a la maniobra, del 2026-08-26, daba 260 mapeables y anticipaba perder 1; no se perdió ninguno). 0 pares quedaron con `job_ref_a = job_ref_b` |
| **Comprobación posterior — sombra drenada** | ✅ `[V]`: `jobhunt.shadow_change_log` con **0** filas sin aplicar (`applied_at IS NULL`) sobre 16.173, y el slot `jobhunt_shadow` activo con retención de pocos KB. El GATE-SOMBRA **no** se invalidó: no hubo que soltar el slot ni re-sembrar el snapshot |
| **Imagen operativa inmutable del core** | ✅ **DESPLEGADA** `[V]` (`f728518` + `ae7fbf2`). Los tres procesos publican `release=ae7fbf2` (comprobado con `printenv RELEASE_SHA` en `core-api`, `core-worker` y `core-capture`), `/v1/ready` responde `authoritative: true` y `core-api` reporta *healthy*. `docker inspect` confirma **0 mounts** en `core-api`. Ver §9.1 |
| **`core-capture`** | ✅ **YA reiniciado** `[V]` desde el 2026-08-26; la avería O-1 está cerrada |
| **NO reiniciar `worker`/`worker-ai`/`backend`** | ✅ **REGLA LEVANTADA** `[V]`. Existía solo para proteger la canonización, que los paró ella misma en su paso 1 y ya terminó. Los tres corren desde el rearranque de hoy |

### 9.1 El arreglo que abrió el fallo simétrico (léelo antes de citar `bf3fbfd`)

`bf3fbfd` (2026-08-26) **no es el cierre definitivo** de la avería de `/v1/ready`, y
cualquier documento que lo presente así está desfasado. Es el caso didáctico de la regla
de oro de §0, encontrado por una auditoría **externa** el 2026-08-27 (P1-3):

| Versión | Qué hacía `_expected_head()` | Fallo |
|---|---|---|
| Antes de `bf3fbfd` | `@lru_cache` sin clave: expectativa fijada para toda la vida del proceso | **Falso ROJO** — dos días de 503 con la BD sana |
| `bf3fbfd` | Releía la cadena del volumen montado, en caliente | **Falso VERDE** — el proceso sirve la release A mientras ficheros y esquema van por la B |
| `f728518` + `ae7fbf2` (vigente) | `_EXPECTED_HEAD` se lee **una vez al importar**, de la misma imagen que trae los handlers; si no se puede leer, el proceso **no arranca** | — |

Lo que cierra la incoherencia no es la lectura sino el **despliegue**: el perfil operativo
ya no monta el código, así que cambiar la cadena exige cambiar la imagen, y eso recrea el
proceso. `/v1/health` publica además `release` + `alembic_expected`, y **las dos sondas** llevan
`authoritative` (G9 P2-A: solo lo tenía `/v1/ready`, y health es la primera del ritual de
verificación). Es **false** en el perfil de desarrollo —código montado: verde
**informativo**, no autorización para operar— y también con `RELEASE_SHA=unknown`, porque
una release que el proceso no sabe nombrar no verifica nada (G9 P2-B). `[V]` — leído en
`jobhunt_core/api/main.py:158-235` y comprobado contra los tres procesos vivos.

### Sigue pendiente

| Qué | Estado verificado |
|---|---|
| **Rotar `GEMINI_API_KEY`** | ⚠ **SIN HACER** `[V]`. El canal está cerrado desde `7ef7d89` (2026-08-26), pero cerrar el canal no borra lo publicado: la clave estuvo en claro en el journal. Acción **del propietario**, no de un agente (esta sesión no toca `.env`) |
| **`core-api` sin healthcheck en producción** | ⚠ **ABIERTO** `[V]`. `docker-compose.prod.yml` y `docker-compose.qnap.yml` definen `core-api` sin bloque `healthcheck:` (comprobado leyendo los dos ficheros). No se tocaron **a propósito**: son de producción y requieren confirmación explícita. El compose base **sí** lo tiene. **Cambio ya redactado y comprobado aplicable** (sin aplicar): `docs/propuesta_g9_composes_prod.patch` |
| **`RELEASE_SHA` sin hornear en producción** | ⚠ **ABIERTO** `[V]` (auditoría G9 P2-B). `ae7fbf2` añadió el build arg SOLO a `docker-compose.yml`. `docker-compose.prod.yml` construye la imagen del core **sin `args:`**, así que en el NAS hornea el default `unknown` del Dockerfile; `docker-compose.qnap.yml` ni siquiera construye (imágenes por `docker load`), de modo que el SHA depende de cómo se construyó la imagen que se carga. Consecuencia: el paso de verificación «todos publican el mismo SHA» se satisfacía trivialmente entre `unknown`s. Desde G9 las sondas responden `authoritative: false` con `release: unknown`, así que el defecto **se ve** en vez de aprobar en silencio. El arreglo de fondo va en el mismo `docs/propuesta_g9_composes_prod.patch`: build arg en prod + nota del `docker save` en qnap |
| **El NAS** | ⚠ **DESACTUALIZADO** `[I]`. Corre imágenes anteriores a toda la jornada del 2026-08-27. Cuando se suban las imágenes nuevas **habrá que aplicar allí la MISMA canonización, en el MISMO despliegue**: el código nuevo emite ya la identidad canónica, y una cosecha con código nuevo sobre datos sin canonizar es pérdida silenciosa + duplicación a la vez (ver el encabezado de `backend/scripts/g3_canonizacion_identidad_arbeitnow_jobgether.sql`). Los dos scripts del repo terminan en `ROLLBACK`; hay que cambiar esa línea a `COMMIT` sobre una copia |
| **Carga y congelado de la cohorte de dedup** | 🔄 **EN CURSO** por otro agente al cierre de esta pasada. `jobhunt.labeled_dedup_cohorts` estaba **vacía** al medirlo `[V]`. **Resultado no documentado aquí: no se conocía al escribir esto.** Va DESPUÉS de la canonización, que ya está hecha, así que el bloqueo que lo retenía ha desaparecido |

---

## 10. Dónde está la versión larga

| Tema | Documento |
|---|---|
| Los nueve ciclos, por ciclo y por repo | `/home/lothar/Public/AUDITORIA_GLOBAL_{CORE,LEGACY,PORTFOLIO}_G1..G8_2026-08-26.md` y `AUDITORIA_BUGS_C1..C9_2026-08-25.md` |
| La fase de optimización | `/home/lothar/Public/OPTIMIZACION_{CORE,LEGACY,PORTFOLIO}_2026-08-27.md` |
| Estado y contadores vigentes | `/home/lothar/Public/ESTADO_Y_HOJA_DE_RUTA.md` **§20** (§19 es la foto previa al 2026-08-27) |
| Decisiones ratificadas del propietario | `/home/lothar/Public/ACTA_DECISIONES_2026-08-26.md` |
| Operación de la sombra y la maniobra de canonización (ya ejecutada) | `jobhunt_core/shadow/RUNBOOK.md` §7 |
| La auditoría externa del 2026-08-27 (veredicto NO-GO, 5 condiciones) | `/home/lothar/Public/AUDITORIA_EXTERNA_BUGS_2026-08-27.md` y `AUDITORIA_EXTERNA_DISENO_2026-08-27.md` |
| Contratos del core | `/home/lothar/Public/CONTRATOS_FASE_{A,B,C}.md` |
