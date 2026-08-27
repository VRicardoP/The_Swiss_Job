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

**Protocolo de sondas** que las auditorías siguieron y conviene mantener: escrituras **solo**
contra `swissjobhunter_test`, en transacción revertida; contra producción **solo `SELECT` y
`EXPLAIN (ANALYZE, BUFFERS)` sobre `SELECT`**; claves Redis con prefijo, borradas al
terminar; **ningún servicio reiniciado, recreado ni reconstruido**; árbol del repo intacto.

> **Trampa de instrumento, medida:** el muestreo de `pg_stat_*` debe hacerse **desde
> conexiones distintas**. Dentro de una misma transacción, `stats_fetch_consistency = cache`
> sirve una instantánea cacheada — la primera medición de la auditoría de optimización dio
> «0 escrituras» por eso.

---

## 8. Afirmaciones falsas VIVAS (no corregidas)

Estas están **abiertas** al cierre de esta pasada. Viven en ficheros de código, que esta
documentación no puede tocar.

| Dónde | Qué afirma | Qué hace en realidad |
|---|---|---|
| **`backend/services/deduplicator.py`**, docstring de `find_semantic_duplicates` `[V]` | «Por eso la puerta se SALTA cuando los idiomas declarados difieren» | **La puerta se aplica SIEMPRE.** `0465681` retiró esa excepción y añadió el comentario que lo explica 30 líneas más abajo, pero **no tocó el docstring**, que sigue describiendo el comportamiento de `a63745c`. Comprobado: `grep -n "language" backend/services/deduplicator.py` no devuelve **ni una línea** |
| **`backend/services/scheduler.py`**, log de resumen `[V]` | «URL check weekly Sun 03:00» | El job es `CronTrigger(hour=3)` — **diario**. El comentario cinco líneas más arriba ya dice «DIARIA a las 03:00 CET (antes: semanal los domingos)»: al cambiar el trigger no se cambió la cadena del resumen |
| **`fbe22f0`**, mensaje del commit de consolidación `[V]` | «1.083 escrituras/s, 42 GB de WAL al día» | Medido con **dos instrumentos independientes que coinciden dentro del 0,6 %**: **559–979 UPDATE/s** (48,3 M/día) y **228 GB/día**. Las cifras válidas son las del informe |
| `backend/scrapers/irishjobs.py`, docstring `[I]` | Los dos hosts «se deduplican por ese `id` de plataforma (misma oferta en ambos)» | Los datos lo desmienten: 35 grupos cross-host con descripción idéntica byte a byte e **ids que difieren** — cada host los numera por su cuenta |
| `backend/services/groq_service.py` `[I]` | «Este es el único borde por el que entra la respuesta del LLM» | En un **cache hit** el resultado va directo a `_apply_llm_result` sin pasar por `_parse_llm_response` |
| `jobhunt_core/delivery.py`, pre-SELECT de `mark_delivered` `[I]` | «las filas que va a tocar el UPDATE de abajo son **exactamente éstas**» | Son un **superconjunto** |

---

## 9. Estado operativo — lo que está pendiente y NO es código

| Qué | Estado verificado |
|---|---|
| **Migración legacy `b3c7d1a95e42`** | ⚠ **SIN APLICAR** `[V]`. La BD está en `a1f2e3d4c5b6` y el head del repo es `b3c7d1a95e42`. Pone `clock_timestamp()` en los defaults de `jobs.first_seen_at/last_seen_at` y `match_results.created_at`; sin ella, `now()` == `transaction_timestamp()` **se congela al ABRIR la transacción** y muchas filas comparten marca temporal al microsegundo |
| **Scripts de canonización de identidad** | ⚠ **SIN EJECUTAR** `[I]`. Limpios y validados desde G7. Ver el orden de CINCO pasos en `jobhunt_core/shadow/RUNBOOK.md` §7 — tiene **dos mitades** y solo una vive en `backend/` |
| **Carga del estrato positivo (187 pares)** | Va **DESPUÉS** de la canonización `[I]`: cargarlo antes graba refs que la maniobra invalida, y `--excluir` no sirve porque **el daño no lo detecta ninguna guarda del loader** |
| **Rotar `GEMINI_API_KEY`** | ⚠ **SIN HACER** `[V]`. El canal está cerrado desde `7ef7d89` (2026-08-26), pero cerrar el canal no borra lo publicado: la clave estuvo en claro en el journal. El `.env` local sigue con `mtime` del **2026-07-02**. Acción del propietario |
| **NO reiniciar `worker`/`worker-ai`/`backend`** | Regla vigente hasta la canonización, que los para ella misma en su paso 1 |
| **`core-capture`** | ✅ **YA reiniciado** `[V]`, fuera de esa regla. Contenedor de 2026-08-26T23:06:59Z; slot con 5.088 bytes retenidos y latido sin variación entre dos muestras: la avería O-1 está cerrada |

---

## 10. Dónde está la versión larga

| Tema | Documento |
|---|---|
| Los nueve ciclos, por ciclo y por repo | `/home/lothar/Public/AUDITORIA_GLOBAL_{CORE,LEGACY,PORTFOLIO}_G1..G8_2026-08-26.md` y `AUDITORIA_BUGS_C1..C9_2026-08-25.md` |
| La fase de optimización | `/home/lothar/Public/OPTIMIZACION_{CORE,LEGACY,PORTFOLIO}_2026-08-27.md` |
| Estado y contadores vigentes | `/home/lothar/Public/ESTADO_Y_HOJA_DE_RUTA.md` **§19** |
| Decisiones ratificadas del propietario | `/home/lothar/Public/ACTA_DECISIONES_2026-08-26.md` |
| Operación de la sombra y la maniobra de canonización | `jobhunt_core/shadow/RUNBOOK.md` |
| Contratos del core | `/home/lothar/Public/CONTRATOS_FASE_{A,B,C}.md` |
