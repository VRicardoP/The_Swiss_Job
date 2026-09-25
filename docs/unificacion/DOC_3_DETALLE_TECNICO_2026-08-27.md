# DOC 3 — Detalle técnico: cómo funciona por dentro

> **⚠ DESFASADO EN PARTE — 2026-08-29.** Este documento es del 27 de agosto y no
> recoge tres cambios posteriores importantes:
>
> 1. **La coordinación crítica del cutover salió de Bash** a
>    `backend/scripts/cutover_coordinador.py` (identidad canónica del recurso,
>    cerrojo, publicación durable del checkpoint y transiciones de fase), tras la
>    tercera reapertura del mismo invariante.
> 2. **`core0036`** añade paradas declaradas del anfitrión y **enmienda qué
>    significa «siete ciclos consecutivos»**: un ciclo ausente y uno rojo dejan de
>    ser lo mismo. Es una enmienda de contrato que **debilita a propósito una
>    propiedad de seguridad**.
> 3. **Holdout de dedup nuevo** (`holdout-dedup-2026-08-30`, etiquetado por un
>    agente independiente) y los cinco invariantes del cutover congelados por
>    escrito en `docs/DEPLOY_NAS.md` §5.3.
>
> Para el estado real y lo que bloquea el proyecto hoy: **`TRASPASO_2026-08-29.md`**.


> **Nivel 3 de 3.** Para cada mecanismo importante: **qué objetivo cubre** y **cómo lo
> cumple**. Asume leídos el [DOC 1 — Blueprint](DOC_1_BLUEPRINT_2026-08-27.md) (el porqué)
> y el [DOC 2 — Componentes](DOC_2_COMPONENTES_2026-08-27.md) (el inventario).
>
> **Fecha:** 2026-08-27 (mañana). `[V]` = comprobado por mí leyendo el código o ejecutando
> contra el sistema vivo. `[I]` = de informe, **no** re-medido. Las cifras `[I]` son
> hipótesis con procedencia, no hechos.
>
> ⚠ **ACTUALIZADO la tarde del 2026-08-27.** Esa misma tarde: se **ejecutaron** las dos
> maniobras que este documento da por pendientes, el `not_ready` de §11.3 se **arregló y
> desplegó** (y el primer intento abrió el fallo simétrico — §11.4, nueva), y el perfil
> operativo del core pasó a **imagen inmutable**. Los pasajes afectados llevan un bloque
> **ACTUALIZACIÓN**. Estado consolidado: `ESTADO_Y_HOJA_DE_RUTA.md` **§20**.
>
> El índice largo de cotas aceptadas vive en `SwissJob/docs/COTAS_Y_DECISIONES.md`. Aquí no
> se repite: aquí se explica **el mecanismo** al que cada cota pertenece.

---

## 1. Identidad de una oferta en el core

**Objetivo.** Que una oferta conserve su identidad cuando el portal corrige su contenido, y
que cambie de identidad —visiblemente— cuando deja de ser la misma oferta.

**Cómo se cumple.** Tres niveles, cada uno con su propia inmutabilidad:

```
source_listings              el SLOT: la ranura estable del anuncio en su fuente
  │                          UNIQUE(source, external_id)  ·  UNIQUE(source, url_normalized)
  │
  └─ source_listing_incarnations   el BINDING slot → vacante EN EL TIEMPO
       │                          el reciclado cierra una y abre otra
       │
       └─ source_listing_revisions  el CONTENIDO raw, versionado por content_hash
                                    UNIQUE(incarnation, content_hash) · OBLIGATORIA
```

Título y empresa **no participan** en la identidad. Una corrección de título produce una
revisión nueva en la misma encarnación del mismo slot: la vacante canónica no se entera.

**El guard de reciclado** (`harvest/identity.py`) es la única puerta por la que la empresa
influye, y es deliberadamente estrecha `[V]`:

```python
def should_recycle(old_identity, new_identity) -> bool:
    """SOLO cuando AMBAS empresas existen y sus tokens difieren.
    Falta de datos → conservador: nueva revisión en la misma incarnación."""
```

Es decir: **la ausencia de dato nunca dispara un reciclado**. Si no sabemos la empresa
vieja o la nueva, se asume continuidad. El sesgo va hacia no partir una vacante por error,
porque partirla crea un huérfano y unirla de nuevo es trabajo manual.

Cuando sí se recicla: se **cierra** la encarnación y nace una vacante nueva con el historial
intacto. Es un *split* visible. Lo que nunca ocurre es una fusión automática: títulos y
empresas difusos van a `dedup_candidates`, jamás a un merge.

**Cotas del mecanismo.**

- El modelo es condición **necesaria pero no suficiente**. La estabilidad real la aporta el
  `external_id` que elige **cada adaptador**. El único provider nativo del core usa
  `slug or url`, y el slug lleva el título dentro `[I]`: la misma clase de identidad
  mutable que el defecto del legacy, ahora en el core. Cada adaptador nuevo tiene que
  **demostrar** identidad estable; el esquema no se la regala.
- Mientras el legacy siga de cosechador, la proyección sombra usa su `hash` como
  `external_id` `[I]`: hereda la identidad mutable. **Migrar los datos no extingue el
  defecto**; extinguirlo requiere que la cosecha pase al core con adaptadores propios.
- Una fuente con URL de listado **constante** también choca aquí: el `UNIQUE(source,
  url_normalized)` hace que el segundo listing se salte — **con aviso, visible**, no en
  silencio `[I]`. Sigue habiendo que arreglar la URL por oferta en la fuente.

**Y en el legacy, para contraste.** La clave es `MD5(title|company|url)` `[I]`. Título
corregido ⇒ hash nuevo ⇒ el `ON CONFLICT ... (hash)` no encuentra la fila ⇒ el INSERT choca
con el índice único de URL ⇒ aborta su *savepoint* por oferta ⇒ la oferta deja de refrescar
`last_seen_at` ⇒ la limpieza la archiva a los 60 días. Cinco pasos, ninguno de ellos un
error visible.

---

## 2. La cosecha del legacy: cuatro capas para parecer humano

**Objetivo.** Traer las ofertas nuevas de cada portal sin que el volumen, el ritmo ni la
huella técnica de las peticiones delaten un robot — y sin evadir a quien nos bloquee.

### 2.1 Incremental por cursor con corte temprano

`SourceCursor` guarda, por fuente y ámbito, una ventana corta de URLs recientes. Antes de
`fetch_jobs`, el pipeline inyecta esa ventana en el scraper; al paginar, si una página
entera resulta ya conocida, se para. El volumen de peticiones pasa a ser **proporcional a
las novedades**, no al tamaño del catálogo.

> **Cota medida, y contradice la premisa declarada del cursor** `[I]`: el corte temprano
> gatea **listados, no detalles**. La función que recoge una página baja el detalle de
> todos los stubs **antes** de evaluar si la página era conocida. Con
> `RATE_LIMIT_SECONDS=2.0` y 20 ofertas por página, **una página íntegramente conocida
> cuesta 20 peticiones y 40 segundos diarios para no aprender nada**. Afecta a `schuljobs`,
> `myscience` y `gastrojob`. Aparcado con evidencia, no por olvido.

### 2.2 Presupuesto explícito

`CrawlerBudgetService` decide, **sin ninguna E/S** `[V]`, dos cosas a partir del historial
del cursor:

- `max_pages_this_run`: si el bootstrap no está completo, la ventana completa de la fuente;
  si lo está, `ceil(media_de_novedades / tamaño_de_página) + margen`, acotado a
  `[1, tope_de_la_fuente]` `[V]`.
- `should_run`: tras N runs seguidos sin novedades, la fuente se salta runs con un intervalo
  que se **duplica por cada run vacío extra**, con tope.

La pureza no es estética: hace el presupuesto trivialmente testeable y evita que una
decisión de política dependa del estado de la red.

**El bug que este mecanismo tuvo, y por qué hay que leerlo antes de tocarlo** `[I]`. El
presupuesto se derivaba de una media móvil **cuyo insumo estaba capado por el propio
presupuesto**: la media nunca podía aprender una demanda mayor que el techo que ella misma
fijaba. Con cosecha *newest-first*, lo que quedaba bajo el horizonte se hundía y **no se
recuperaba jamás**. Y nada lo contaba: no había ofertas saltadas (nunca se descargaron) ni
error (el run fue «correcto»). Pérdida silenciosa por realimentación.

El arreglo reutiliza una señal que ya existía, `_stop_reason`: si el run agotó el
presupuesto **sin** haber cortado por página conocida, terminó «con hambre», y la pasada
siguiente reabre el bootstrap. El caso agudo fue `tes`, donde el servidor sirve **una oferta
por página**: el margen de seguridad de «una página» era **una oferta**.

### 2.3 Capa de sigilo

`scraper_stealth.py` expone funciones **puras**: cabeceras de Chrome realistas (con *client
hints* y `Sec-Fetch`), retardo con *jitter* (evita el delator más simple, que es el
intervalo perfectamente regular), y detección de **bloqueo blando** — HTTP 200 con cero
datos y un marcador anti-bot en el cuerpo, que sin esta comprobación se contaría como un
run correcto y vacío.

Rendimiento del mecanismo: `myscience` y `financejobs` estaban dados por perdidos y
**volvieron a funcionar** sólo con cabeceras realistas `[I]`. `medjobs` no: Cloudflare le
impone un desafío duro que el Playwright endurecido local no supera, y está fuera del
registro hasta que haya un navegador sigiloso remoto.

### 2.4 No evasión

`ComplianceEngine` comprueba **antes** de cada cosecha. Tres bloqueos consecutivos
desactivan la fuente automáticamente. La política es explícita: si un portal nos bloquea,
se registra y se para; no se busca la vuelta.

Detalle operativo que muerde al añadir un scraper: **sin fila en `source_compliance`,
`can_scrape=False`** y el scraper queda bloqueado en silencio `[I]`. Sembrar esa fila es
parte del alta, no un extra.

### 2.5 Cotas del conjunto

- **Los 15 scrapers corren en serie.** Paralelizarlos bajaría de 321 s a ~140 s (2,3×), pero
  **15 conexiones simultáneas desde una IP son una huella distinta de 15 secuenciales**
  `[I]`. Es una llamada de criterio del dueño sobre el perfil de riesgo, no una
  optimización que un auditor pueda dar por buena. **No aplicado.**
- **Degradación parcial cuenta como `ok`.** `adzuna` mete tres subfuentes bajo una clave: si
  una de las tres trae ofertas, el run sale `ok` `[I]`. Política declarada.
- **`Retry-After` se ignora**; siempre retroceso exponencial propio `[I]`. Razón escrita:
  «sin medir, no lo priorizo».

---

## 3. Normalización de salarios

**Objetivo.** Sacar un rango salarial en CHF de un texto libre en cuatro idiomas, sin
inventarse cifras.

**Por qué merece su propia sección.** Este parser **se rompió cuatro veces por el mismo
sitio**: la regla de desempate. Y las cuatro versiones se validaron contra el mismo
material —los 637 valores de `jobs.salary_original`, todo ASCII generado por máquina— que
**no puede refutar nada**: no contiene ni una referencia, ni un año, ni una escala salarial,
ni un `bis` `[I]`. De ahí que hoy exista un corpus de 62 anuncios de prosa realista.

**La regla vigente, única** `[V]`. Compiten tres patrones —divisa en los dos extremos,
divisa sólo a la derecha, y `plain` sin divisa— y **gana el primero del texto**. `plain` es
el único que puede casar ruido puro, así que si va primero **y hay otro candidato** debe
superar **las dos** pruebas:

1. **Magnitud** (`_low_looks_like_salary`).
2. **Ancla léxica**: abrir el texto, o ir tras una palabra de sueldo dentro de una ventana
   de **28 caracteres**.

Sin el ancla, un año o una referencia secuestran el parseo:
`Réf. 2025-0043 — Salaire CHF 92'000 - CHF 108'000` daba `(2025, 43)`, y tras el
intercambio se persistía `salary_min_chf = 43` `[I]`.

**La cláusula «y hay otro candidato» no es cosmética** `[V]`: exigirle magnitud y ancla a
`plain` **siempre** rompe tres filas reales — `12-42508 EUR`, `21-42508 EUR`,
`720-2400 EUR`.

**Cuatro cotas como `xfail(strict=True)`**, estrictas a propósito `[V]`: si un ciclo futuro
las arregla, la suite avisa con un XPASS en vez de dejarlo pasar. Un `pytest.xfail()`
imperativo cortocircuitaría el test y **jamás** daría esa señal.

**La regla que más caro sale olvidar, y aplica a TODO scraper** `[I]`:

> Deja `salary_*_chf = None` y pasa `salary_original` + `salary_currency` +
> `salary_period`. El normalizador convierte. **Prellenar los `_chf` con una divisa que no
> sea CHF los guarda sin convertir** —`normalize_salary` tiene retorno temprano si ambos
> `_chf` están puestos— y eso es corrupción de **~2000×** en euros por hora.

Otras cotas: moneda desconocida ⇒ importe **descartado** (mejor sin salario que un salario
×100; los factores reales medidos eran ZAR ≈ ×21 e INR ≈ ×106, y había **107 filas ya
corruptas** en `jobgether` `[I]`); `_MAX_SALARY_TEXT = 200`, que es la cota de la columna y
contiene un O(n²) medido en **37 s** con una entrada de 16 000 nueves `[V]`.

---

## 4. Deduplicación

**Objetivo.** Que la misma vacante publicada en varios portales aparezca una vez, **sin
desactivar nunca una vacante real**.

La asimetría del objetivo lo gobierna todo: `mark_duplicate` escribe `duplicate_of` **y
`is_active=False`**. Un duplicado que sobrevive se ve en el catálogo y molesta; una vacante
real desactivada desaparece del catálogo y del matching, y nadie se entera. Este proyecto ya
perdió **664 vacantes reales** por un dedup mal calibrado `[I]`. Todo lo que sigue está
sesgado hacia el lado seguro por esa factura.

### 4.1 Tres niveles

1. **Exacto** — URL + identificador de fuente. Cubre los reposts dentro de la misma fuente.
2. **Difuso** — hash MD5 de título + empresa normalizados.
3. **Semántico** — coseno sobre el vector multilingüe, con umbral 0,95 `[V]`.

### 4.2 El nivel semántico, y su cota estructural

El nivel 3 no puede ser mejor que su vector, y el vector tiene un techo `[I]`:
`paraphrase-multilingual-MiniLM-L12-v2` tiene **`max_seq_length = 128` tokens** y el texto
que se embebe **no se trunca**, así que el encoder ve el arranque de la descripción, que
suele ser **boilerplate del empleador**. Medido:

```
boilerplate x0 (  0 chars) -> coseno = 0.4502
boilerplate x2 (344 chars) -> coseno = 0.9757   ← DUPLICADO
boilerplate x4 (688 chars) -> coseno = 1.0000   ← vectores IDÉNTICOS
```

…entre «Sachbearbeiter Finanzbuchhaltung» y «Gärtner Grünflächenunterhalt». A umbral 0,95,
**el coseno deja de medir identidad y mide cuánto boilerplate comparten**. Subir el umbral
no arregla nada. Y el vector está persistido en pgvector y lo consumen matching **y** dedup:
cambiar el texto embebido exigiría re-embeber el corpus entero.

**La defensa real no es el coseno: es una puerta léxica.** Se exige además un solape mínimo
de léxico entre los títulos (`SEMANTIC_DEDUP_TITLE_OVERLAP_MIN = 0.3` `[V]`), más
comprobaciones de conflicto de cantón y de salario.

Cotas conocidas de esa puerta `[V]`:
- *Falso positivo:* dos vacantes del mismo empleador cuyos títulos comparten un tercio del
  léxico («Sachbearbeiter Finanzbuchhaltung» vs «Sachbearbeiter Debitoren») pueden casar si
  además comparten cantón y no declaran salario.
- *Falso negativo:* rechaza duplicados reales cuando el título cambia de forma sin cambiar
  de sentido — «Primarlehrperson 60%» vs «Lehrperson Primarstufe 60%» (coseno 0,9487, solape
  0,250).

### 4.3 El dedup cross-idioma: retirado, y por qué no basta con reabrirlo

Existió una excepción que **saltaba la puerta léxica** cuando los idiomas declarados
diferían, para recuperar la misma vacante publicada en DE y en FR. Se retiró entera. Tres
motivos, los tres medidos con el encoder real `[V]`:

1. **La separación estaba invertida.** Una maestra de primaria (DE) y un contable (FR) del
   mismo municipio puntúan **0,8220** — **por encima** del duplicado auténtico, que puntúa
   **0,8195**. No existe ningún umbral que recoja el segundo sin recoger el primero.
2. **No servía de nada igualmente.** A umbral 0,95 el prefiltro SQL mata el par una capa
   antes: la rama era **inalcanzable**. Sobre 200 activas, los pares cross-idioma son **0 a
   los cuatro umbrales** probados.
3. **El precio de equivocarse no es cosmético** — las 664 vacantes.

**Cota aceptada por escrito:** la misma vacante en dos idiomas **no** se deduplica por esta
vía. Lo cubre el hash difuso cuando el título coincide.

**Si alguna vez hace falta reabrirlo, bajar el umbral es exactamente lo que no hay que
hacer.** El discriminante que sí cruza idiomas es el **coseno de los títulos solos**, sin
boilerplate: medido sobre 8 pares reales DE/FR y 8 falsos del mismo municipio, separa en el
sentido correcto — `min(reales) = 0,6067 > max(falsos) = 0,5033` `[V]`, al revés que el
coseno del texto completo. Haría falta ese discriminante **y** su propio umbral **y** abrir
el prefiltro; y por el motivo (2), hoy no tendría ningún par sobre el que actuar.

### 4.4 El índice HNSW y su modo de fallo

El barrido semántico usa el índice vectorial, no un escaneo. La optimización que lo
consiguió midió **93,2 → 2,84 ms** por llamada (32,8×) y **46,6 → 1,4 s** por cosecha, con
un diff sobre el corpus entero (8 162 candidatos) de **0 diferencias** `[I]`.

Pero un índice aproximado puede truncar. La guarda `[V]`: si **todas** las filas devueltas
por el índice caen dentro del radio de búsqueda, el conjunto podría estar truncado y el
canónico podría cambiar, así que se cae a un barrido exacto. La comparación se hace contra
**lo que el índice devolvió de verdad**, no contra el tope pedido — porque `hnsw.ef_search`
(40) manda sobre el `LIMIT`. Comparar contra el `LIMIT` habría dejado el fallo invisible.

---

## 5. Matching en tres etapas

**Objetivo.** Ordenar el catálogo entero por relevancia para un perfil, con un coste de LLM
acotado y sin que el tope de coste pierda ofertas buenas.

**Cómo se cumple** `[V]`:

| Etapa | Qué hace | Sobre qué |
|---|---|---|
| 1 | Coseno pgvector | **Todas** las ofertas activas, **sin `LIMIT`** |
| 2 | Puntuación multifactor | Todas las candidatas de la etapa 1 |
| 3 | Re-ranking por LLM | Adaptativo, ver abajo |

**La ausencia de `LIMIT` en la etapa 1 es deliberada** `[V]`: es lo que permite que sea el
**umbral** quien decida en la etapa 2, en vez de un top-K fijo. El número de resultados
guardados es variable; lo fija `MATCH_SCORE_THRESHOLD`, que vale **42.0** en el `.env`
`[V]` — no el 35.0 del valor por defecto de `config.py`. Al citarlo, cita el `.env`.

**Los pesos de la etapa 2 son SEIS, no cinco** `[V]`:

```python
DEFAULT_WEIGHTS = {
    "embedding": 0.35, "salary": 0.15, "location": 0.10,
    "recency":   0.15, "llm":    0.15, "language": 0.10,
}
```

**La etapa 3 es adaptativa** `[V]`: si el conjunto cualificado es ≤ `MATCH_LLM_RERANK_MAX`
(**150**), se re-rankean **todas**, sin tope — que es el caso normal. Por encima, «modo
avalancha»: se limita a `MATCH_LLM_RERANK_TOP` (**50**).

> **Cota del modo avalancha, y no es recuperable** `[I]`: la cabeza se elige por el
> `score_final` de la etapa 2, que se calcula con `llm_score = 0.0`. Una oferta que quede
> bajo el puesto 50 tiende a quedarse ahí corrida tras corrida. El tope es global por
> usuario y por corrida, no por portal.

**Economía del LLM.** El tamaño de lote de re-ranking es **10**, y no es arbitrario: 3 728
tokens × `GROQ_CONCURRENCY=2` = **93 % del límite de 8k por minuto** del tramo gratuito
`[I]`. Subirlo a 20 lo reventaría. La conclusión escrita es que **la palanca es la caché, no
el lote** — y en efecto, cambiar la caché de re-ranking de *por lote* a *por oferta* la llevó
de **cero claves vivas** (nunca acertaba) a acertar `[I]`.

**Un lote degradado jamás se cachea** `[I]`: verificado con Redis real, tras forzar una
degradación quedan 0 claves. Evita que un fallo del LLM se sirva durante 7 días.

**Degradación en cascada.** Groq es el primario del re-ranking y de la traducción; Gemini es
el respaldo. Si Groq devuelve 401 —clave caducada o revocada— el servicio **aborta rápido
con un error explícito** en vez de tragárselo y reintentar en vano. El síntoma visible al
usuario de una `GROQ_API_KEY` caducada es «los títulos no se traducen»; el diagnóstico es
buscar `HTTP 401` en el log del backend `[I]`.

---

## 6. La sombra, parte 1: captura CDC

**Objetivo.** Replicar cada cambio de las tablas legacy al core **sin perder ninguno**
(RPO = 0), sobre una base de datos compartida a la que no se le puede hacer daño.

**Cómo se cumple** `[V]`. `core-capture` es un **proceso dedicado**, no una tarea Celery, y
su bucle central es:

```
recibir cambio → INSERT en jobhunt.shadow_change_log ... ON CONFLICT DO NOTHING
              → COMMIT
              → y SOLO DESPUÉS  send_feedback(flush_lsn)
```

**El ack va después del commit.** Esa ordenación es toda la garantía: la pérdida es
imposible por construcción, porque el slot re-entrega lo que no se ha confirmado. La
re-entrega **sí** es posible por diseño (por ejemplo al reanudar en el LSN del último commit
aplicado), y la absorbe la clave primaria del staging, `(lsn, seq_in_tx)`.

El bootstrap es igual de cuidadoso: se crea el slot, se exporta un snapshot **consistente
con el LSN del slot**, y el backfill completo va **antes** de arrancar el streaming, porque
el snapshot exportado por una sesión deja de ser usable en cuanto esa sesión ejecuta otro
comando. Si el esquema legacy no está listo, se espera con retroceso **sin crear nada**: un
slot creado y luego abandonado en cada vuelta de un ciclo de reinicios sería peor que no
tener sombra.

### El healthcheck: ocho señales, y por qué las dos nuevas

`--health` ejecuta ocho comprobaciones en cascada; la primera que falla decide `[I]`:

| # | Señal | Umbral | Qué detecta |
|---|---|---|---|
| 1 | Conexión al core | 5 s | core caído |
| 2 | Slot presente | — | slot borrado ⇒ continuidad WAL perdida |
| 3 | Slot `active` | — | consumidor caído mientras el slot retiene WAL |
| 4 | WAL retenido | **2 GiB** | fallo bruto. **Es lento para un fallo lento** |
| 5 | **Ritmo del latido** | **10/s**, ventana ≥30 s | el lazo de cola del heartbeat |
| 6 | **Progreso** hacia `confirmed_flush` | suelo 512 MiB, ventana 1800 s | consumidor que late pero no gana terreno |
| 7 | Frontera registrada + edad del latido | 26 h | bootstrap incompleto |
| 8 | Staging drenado | 2 h | proyector colgado — vive en **otro** contenedor |

Las señales 5 y 6 son de 2026-08-27 y son el corolario de la avería más instructiva del
proyecto:

> **La frescura del latido no desmentía la avería: ERA la avería.** `core-capture` estuvo
> **cinco días** ejecutando una versión anterior a su propio arreglo —**con el fichero ya
> corregido dentro del contenedor**—, escribiendo entre 559 y 979 actualizaciones por
> segundo sobre su fila de estado y generando **228 GB de WAL al día** `[I]`. El
> healthcheck estuvo **verde los cinco días** porque comprobaba que el latido fuera
> reciente. A 559/s, el latido era fresquísimo.

La causa raíz no es de monitorización: **Python importa cada módulo una vez**. El proceso
arrancó 2 h 14 min antes del commit del arreglo y nadie lo reinició. `grep` al fichero no
responde qué código corre; sólo lo responde la fecha de arranque del proceso contra la del
commit. Y hay dos trampas más una capa abajo `[V]`: `docker compose ps` muestra la
antigüedad de **creación** del contenedor, no la del proceso (hoy dice «4 weeks ago» para
un proceso de 9 horas), y `RestartCount` sólo cuenta reinicios de la política, así que un
`stop`+`start` manual lo deja en **0**.

La separación entre sano y averiado, con el mismo instrumento, es enorme: latido **0,23/s
sano** frente a **559–979/s averiado**; retención **0,7–14 MB oscilando** frente a **2,6 GB
creciendo a 2,1–3,0 MB/s** `[I]`. El techo de 10/s queda 43× por encima de lo sano y 56× por
debajo de lo averiado: no hay zona gris.

**La señal 5 usa una marca re-anclable**: cualquier mejora re-ancla, de modo que una ráfaga
legítima que luego se drena **no puntúa**; sólo puntúa quedarse por encima del suelo **sin
mejorar** durante toda la ventana. Con el déficit medido, eso delata el lazo en ~35 minutos
en vez de en cinco días.

Y el muestreo se persiste en un fichero temporal **dentro del contenedor, nunca en la base
de datos**: escribir de más es justo el pecado que esta sonda vigila. Fichero ausente o
corrupto se trata como «primera vez», jamás como *unhealthy*.

**Estado verificado hoy** `[V]`: proceso levantado 2026-08-26T23:06:59Z, slot con 5 088
bytes retenidos, latido sin variación entre dos muestras, `shadow_change_log` con **0 filas
sin aplicar**. La avería está cerrada.

---

## 7. La sombra, parte 2: proyección

**Objetivo.** Traducir los cambios legacy al modelo del core **sin abrir rutas de escritura
nuevas** — es decir, usando el mismo sumidero que usaría una cosecha nativa, para que lo que
se mide sea el core de verdad y no un atajo.

**Cómo se cumple.** `project_pending` consume `shadow_change_log` en orden estricto
`(lsn, seq_in_tx)` por lotes de 500 `[I]`, con *single-flight* por lock consultivo (clave
distinta de la del ciclo, para que uno no bloquee al otro). Cada lote deja constancia de su
**intención** antes de aplicarse y se **sella** al terminar, de modo que un lote huérfano
—proceso muerto a mitad— es recuperable en el arranque siguiente.

**La idempotencia es por elemento, no por lote** `[I]`. Es una distinción que costó un
incidente: en la entrega, los *marks* se persisten **por entrega**, no al final del lote,
porque antes **un solo evento venenoso condenaba a sus hasta 99 vecinos ya entregados**.

### El riesgo diferido que conviene conocer

`job_ref` identifica al **slot, no a la vacante** `[I]`. Medido: 569 `source_listings`
`legacy:*` con más de una vacante. El arreglo real —referenciar por `vacancy_id`— es otro
trabajo, y está **diferido** con esa etiqueta. Importa porque el oráculo se direcciona por
`job_ref`.

---

## 8. El GATE-SOMBRA

**Objetivo.** Decidir el go/no-go de la Fase B con una evidencia que no dependa de que el
sistema viejo tenga razón.

### 8.1 El ciclo

Un ciclo es una ventana de calendario `[06:00, 06:00+1d)` en `Europe/Zurich`. A las 06:05,
`run_cycle` orquesta `[V]`: proyectar el staging (con reintentos acotados) → verificar que
está **drenado** → computar las métricas del ciclo **cerrado** → purgar el staging →
evaluar los gates y actualizar el contador.

Si el drenado no se completa, **el ciclo no se computa**. Es fail-closed: un ciclo sin datos
no puede llamarse verde. Todo el ciclo va bajo un lock consultivo, y un ciclo ya sellado no
se recomputa.

### 8.2 Las métricas y sus umbrales

Verificados en `shadow/metrics.py` `[V]`:

| Métrica | Umbral | Tipo |
|---|---|---|
| `ndcg@10` (por perfil) | ≥ **0,60**, y ≥ ndcg legacy − 0,05 | gate |
| `dedup_precision` | ≥ **0,95** | gate |
| `dedup_recall` | ≥ **0,40** | gate |
| `falsos_negativos` | 0 si hay <50 juicios relevantes; ≤ 2 % si ≥50 | gate |
| `perdida` | **0**, estricto | gate |
| `outbox_dead` | **0**, estricto | gate |
| `outbox_lag_p99` | ≤ **900 s** (3× la cadencia de despacho) | gate |
| `latencia_p95` | ≤ **3600 s** | gate |
| `reenlace_pct` | ≤ **5 %** | alerta |
| `labels_ready` | precondición del oráculo | gate |

Racha exigida: **`GATE_CYCLES_REQUIRED = 7`** ciclos consecutivos `[V]`.

> **Precisión que conviene no confundir:** el *umbral* de `dedup_precision` en código es
> **0,95** `[V]`. El valor **medido** del holdout es 1,000, y es a ese valor medido al que
> se refiere el acta cuando dice «precision 1.000 sigue vinculante». Son cosas distintas: si
> citas «1.000» como umbral, estás citando un resultado, no una regla.

**Por qué `dedup_recall` bajó de 0,90 a 0,40.** No es una rebaja de exigencia por
conveniencia: es el **techo demostrado** del examen congelado. Las señales que faltarían
para subirlo **no existen en los pares históricos**, y esperar a `apply_url` no puede
reverdecerlo porque los pares históricos carecen del dato. La única vía de re-subir el
listón está escrita: **re-etiquetado ciego independiente** del estrato positivo (un agente
de contexto limpio sin acceso a las etiquetas existentes, o el propietario), con acta y con
tope de concentración por clúster. El cambio entró **con su propia acta dentro del código**,
como comentario referenciado `[V]`.

**Los umbrales se persisten con cada ciclo.** Cada ciclo guarda su fila `gate_umbrales`, y
`evaluate_gates` usa los umbrales **de esa fila**, con las constantes vigentes sólo como
respaldo clave a clave `[V]`. Consecuencia: **cambiar una constante no recolorea ciclos ya
sellados**. Es lo que impide reescribir el acta a posteriori.

### 8.3 La elegibilidad — la razón por la que el contador está en 0/7

Un ciclo sólo suma si su ventana **empezó después** del congelado de la cohorte de
evaluación de dedup, y el código es explícito `[V]`:

```python
frozen_at = await dedup_cohort_frozen_at(session, DEDUP_EVAL_COHORT)
eligible = frozen_at is not None and cycle_bounds(cid)[0] >= frozen_at
```

**Sin cohorte congelada, `frozen_at is None` ⇒ ningún ciclo es elegible.** Y la función que
lo lee es **fail-closed**: un sello sin *manifest* real —jsonb vacío, o un tipo que no sea
objeto— no cuenta como congelado, porque «la elegibilidad no se activa con un freeze sin
acta».

Consulté la tabla: **`labeled_dedup_cohorts` tiene 0 filas** `[V]`. Ejecuté el informe del
gate: **0/7, todos los ciclos `INELIGIBLE`** `[V]`.

Esto no es un fallo; es la salvaguarda funcionando. Pero significa que **el reloj del gate
no ha empezado**, y que el orden de las maniobras pendientes (canonizar → cargar el estrato
→ congelar la cohorte) es lo que lo arranca.

> **ACTUALIZACIÓN (tarde del 2026-08-27).** El primer eslabón está hecho: la canonización se
> ejecutó (`2462717`), y con `labeled_dedup_cohorts` todavía vacía, que era justo la ventana
> que necesitaba. Cargar el estrato y congelar la cohorte estaban **en curso** por otro
> agente al escribir esto: **su resultado no se documenta aquí porque no se conocía**. El
> reloj del gate, por tanto, sigue sin arrancar.

### 8.4 La «gracia», o el mecanismo que se reinterpretó cuatro veces

**Objetivo.** Distinguir un hueco legítimo —una oferta legacy sin slot en el core porque el
proyector **no debía** tener slot abierto— de una pérdida real.

**Criterio definitivo** `[V]`, literal del docstring: la gracia exige **evidencia positiva**
de que el proyector obró bien; **la ausencia de evidencia nunca la concede** (*ausencia de
evidencia ≠ evidencia de ausencia*). Dos fuentes, en orden de prioridad:

1. **El último cambio APLICADO** del pk, espejando el predicado propio del proyector:
   - último aplicado = **cierre** ⇒ no había slot que crear ⇒ **gracia**;
   - último aplicado = **apertura** y aun así no hay slot ⇒ el proyector tuvo su
     oportunidad y no la usó ⇒ **pérdida**. Esta rama **manda** sobre la 2.
2. Sin ningún cambio aplicado que consultar, el **estado durable**: si el pk tiene slot
   `legacy:*` ⇒ gracia; si no hay slot **ni** cambio aplicado ⇒ **pérdida**.

**Las cuatro interpretaciones anteriores, cada una con su modo de fallo reproducido** `[I]`:

| Criterio | Modo de fallo |
|---|---|
| Por `first_seen_at` | Falso **rojo** en reactivaciones |
| «Existe un cambio pendiente» | Falso **verde**: una pérdida real enmascarada por un UPDATE rutinario |
| Por la **forma** del slot | Falso **rojo** otra vez |
| Por `u.pk IS NULL` | Falso **verde** que **la purga de retención fabrica sola** — en el clúster real, 6 979 de 10 805 jobs ya no tenían fila en el log |

El último es el más instructivo: el criterio era correcto el día que se escribió y se
volvía falso solo, con el paso del tiempo, por acción de otro subsistema. El docstring lo
dice hoy en su encabezado: *«lleva cuatro ciclos reinterpretándose»*.

---

## 9. Entrega: el outbox y su despachador

**Objetivo.** Entregar cada evento a cada destino al menos una vez, sin entregar
indefinidamente lo que no se puede entregar, y sin que un mensaje malo bloquee la cola.

### 9.1 Emisión

`outbox.emit` es el **único** camino: `event_id` determinista (uuid5), inserción **en la
misma transacción** que la escritura de negocio, `ON CONFLICT DO NOTHING`, y una fila de
entrega por destino. La atomicidad con la escritura de negocio es lo que impide el par de
fallos clásico —evento sin hecho, o hecho sin evento—.

### 9.2 Claim con lease

El despachador reclama con `FOR UPDATE SKIP LOCKED` más un lease renovable, y ordena con un
**orden total y determinista** `[V]`:

```sql
ORDER BY d.next_attempt_at ASC NULLS FIRST, d.event_id, d.destination
```

El desempate por `(event_id, destination)` no es decorativo: hace que la **cabeza de la cola
sea la misma en cada vuelta**, y esa misma cabeza es la que mira el retirador de veneno — de
modo que retira exactamente la fila que el despachador intenta transportar primero.

### 9.3 `attempts` frente a `claims`: por qué hacen falta dos contadores

**`attempts` = transportes EJECUTADOS**, y se consume en el **resultado**, nunca en el
claim `[V]`. La garantía que compra es «no gastar intentos sin transporte»: una fila
reclamada y no transportada no debe acercarse al dead-letter.

Pero esa garantía abría un agujero exacto `[V]`: un payload que **mata al proceso** del
despachador (OOM, segfault) nunca marca resultado, luego **no consume `attempts`**, luego el
dead-letter por agotamiento **jamás llega** — y como el claim ordena por `next_attempt_at
NULLS FIRST`, ese mensaje ocupa la **cabeza de la cola** y bloquea al resto. Un veneno con
inmunidad permanente.

De ahí un contador propio: **`claims` = reclamos consecutivos SIN resultado**, que vuelve a
0 en cuanto la entrega produce cualquier resultado, bueno o malo. Y **dos dead-letters
distintos** `[V]`:

| Retiro | Condición | Significa |
|---|---|---|
| `retire_exhausted` | `attempts >= 8` | el destino está caído |
| `retire_poisoned` | `claims >= 25` | el payload mata al proceso |

**Por qué 25** `[I]`: con el beat cada 5 minutos son ~2 h de ciclo de reinicios
ininterrumpido sobre el **mismo** mensaje; y como **triplica** `MAX_ATTEMPTS`, un destino
simplemente caído siempre muere antes por la vía normal. El backfill inicial usó
`claims = attempts`, una **cota inferior honesta** que nunca mete una fila sana en el umbral
de veneno.

### 9.4 Lo que este módulo enseña sobre lecturas y locks

`mark_delivered` lleva un pre-`SELECT ... FOR UPDATE` que existe por una razón sutil y que
se equivocó **dos veces en direcciones opuestas** `[V]`:

- Primero, el aviso de «lease robado» se leía del `RETURNING` del UPDATE, que evalúa sobre
  la fila **nueva** — y esa misma sentencia ya había puesto `lease = NULL`. La comparación
  era cierta **siempre**: el aviso sonaba en el **100 %** de las entregas sanas. Un operador
  que aprendiera a ignorarlo dejaría pasar el caso real.
- El arreglo fue un self-join que leía el **snapshot** de la sentencia, sin lock. En READ
  COMMITTED, el snapshot se toma al empezar: el UPDATE espera el lock del ladrón, y al
  desbloquearse re-evalúa sobre la fila nueva, pero el escaneo del self-join **no participa**
  de esa re-evaluación y sigue devolviendo la versión anterior. Resultado: el aviso **no**
  sonaba justo en el entrelazado real que existe para delatar. Falso positivo permanente
  cambiado por falso negativo en el único caso que importa.

La versión vigente lee el lease previo **con lock**, que devuelve la versión más reciente
confirmada. Y lleva el **mismo filtro de estado** que el UPDATE, para que una fila ya
terminal se descarte **antes** de pedir su lock: medido, **1,00 s → 0,03 s** `[I]`.

**Cotas declaradas del módulo:**
- **El deadlock entre sentencias existe y se decide NO tocarlo** `[I]`: se reprodujo, «su
  dirección de fallo es benigna y su frecuencia medida es cero» (0 en 600 ciclos). Sólo se
  unifica el orden de lock **dentro** de cada sentencia, que es gratis. Razón escrita:
  «forzar el orden dentro de `retire_poisoned` es lo que rompió esta zona en G5, G6 y G7».
- **El modelo de locks descansa sobre una invariante del llamador** `[I]`: «un solo *mark*
  por iteración». Ningún sitio del módulo la impone. Se acepta hoy; **la Fase C la rompería**
  en cuanto el transporte pase a HTTP y aparezca la tentación de agrupar.

---

## 10. La costura: cómo se decide quién sirve cada petición

**Objetivo.** Mover una capacidad al core de forma reversible, por usuario, sin reiniciar
nada y sin que una caída del core se lleve por delante la aplicación.

### 10.1 Resolución del modo

`resolve_mode(db, capability, profile_id)` `[V]`. Precedencia: **fila exacta**
`(consumer, profile, capability)` → **fila comodín** del consumer → **`local`** por defecto.

La **fila comodín** usa `profile_id = UUID(int=0)` como centinela, no `NULL`, porque la
clave primaria compuesta exige `NOT NULL`. Aplica al consumer entero: sirve para endpoints
sin contexto de perfil (el catálogo anónimo) y para los valores por defecto del consumer.
Es el mecanismo con el que se hará el **flip global del catálogo** en la Fase D: el catálogo
es corpus global sin propietario, así que un canary por perfil no aporta nada.

**La caché y sus carreras** son la parte fina del mecanismo. Hay caché en proceso con TTL
corto e invalidación **transaccional** —sólo tras confirmar el commit, para que un lector
nunca re-cachee el valor antiguo después de publicarse el nuevo—. En el portfolio hay además
un contador de generación `[I]`: `resolve_mode` lo captura **antes** del SELECT y, si cambió
durante el `await`, **descarta** la lectura y reintenta; agotados los intentos, **falla
cerrado con 503** en lugar de servir una lectura posiblemente rancia.

Y la decisión de diseño que más se agradece en una incidencia: si la tabla de routing es
ilegible y no hay conocimiento positivo previo, se devuelve **503**, **nunca se degrada a
`local`**. Asumir `local` convertiría en silencio un `core_primary` configurado en servir
cachés locales rancias — que es exactamente el fallo que un cutover no puede permitirse.

### 10.2 El fallback, y dónde deliberadamente no lo hay

| Modo | Catálogo / matching / perfiles | Candidaturas / documentos / colegios |
|---|---|---|
| `local`, `shadow` | implementación local | local |
| `core_read` | **core con fallback al local** | local (equivalente a `local`) |
| `core_primary`, `rollback_pending` | **core, sin fallback** | core sin fallback (portfolio) / local (SwissJob) |

El envoltorio con fallback cae al local ante dos condiciones distintas y las trata distinto
`[I]`: si el `/v1` **no soporta** la operación es una cota de contrato, esperada, y se
registra a nivel **DEBUG**; si el core **no está disponible** se registra a nivel
**WARNING** — porque es la única señal accionable del canary. Mezclar ambas en el mismo
nivel habría convertido la señal útil en ruido de fondo a ritmo de tráfico.

En `core_read` el fallback es seguro porque el legacy **sigue escribiendo** y su copia está
completa. En `core_primary` no lo es: servir local sería mentir, y escribir en local sería
un segundo escritor.

Para los durables del portfolio, `core_read` se declara **equivalente a `local`** `[I]`, y la
razón es de producto: un canary de lecturas sobre una réplica sin las escrituras vivas
rompería *read-your-writes* — la tarjeta que el usuario acaba de crear no aparecería en su
Kanban.

### 10.3 La anti-mezcla de páginas

Un detalle que sólo aparece cuando el fallback y la paginación coinciden `[I]`. Si la página
1 la sirvió el core (cursor keyset opaco) y la página 2 la sirviera el local (offset), el
usuario vería un revoltijo de dos corpus con dos órdenes distintos. La defensa: un registro
en memoria de proceso que recuerda **qué motor sirvió la página 1** de cada consulta, con
LRU acotado y TTL, indexado por IP de cliente y por un digest de la consulta más un **token
opaco de secuencia** que el propio frontend reenvía. Si la página 2 llega con offset y no
consta qué motor sirvió la primera, **falla cerrado**.

### 10.4 El gate anti-doble-motor

`LEGACY_OWNED_MODES = (local, shadow)` `[V]`. Sólo en esos dos modos actúan los schedulers
del legacy. La etapa de matching de la cosecha diaria consulta este predicado y **se omite
entera** si ningún perfil activo pertenece al legacy `[I]`.

El caso que justifica la lista es `rollback_pending`: ahí el core **sigue** siendo el
escritor hasta el replay final. Si el legacy actuara, el usuario recibiría matching y correo
duplicados durante una vuelta atrás, que es el peor momento posible para añadir ruido.

---

## 11. Procesos de larga vida y estado en memoria

Hay un patrón que ha mordido tres veces en este proyecto, y merece tratarse como una clase
de fallo con nombre: **un proceso de larga vida sirviendo una foto congelada del mundo.**

### 11.1 La garantía de «un solo proceso» no estaba donde se creía

Se creía que la daba una guarda de test que comprobaba que uvicorn no viera
`WEB_CONCURRENCY` ni `UVICORN_WORKERS`. No: **lo que importa es cuántos procesos arranca
uvicorn, y eso no lo contaba nadie** `[I]`. La garantía real vive en el **`--workers 1`
explícito del entrypoint** `[V]`: con `workers` explícito, la rama que lee la variable de
entorno no se ejecuta, y las tres puertas se cierran de una vez.

La guarda vigente **cuenta procesos de verdad**: arranca un uvicorn real con las tres
puertas abiertas y cuenta las líneas de arranque — 4 con la mutación, 1 sin ella `[I]`.

Importa porque de ese único proceso cuelgan cuatro subsistemas, y el cuarto es el limitador
de ritmo, que usa almacenamiento **en memoria**: o sea, estado de proceso. Con dos procesos,
la puerta anti-fuerza-bruta del login pasa a permitir el doble de intentos. El README omitía
ese subsistema de su lista.

### 11.2 O-1: el healthcheck verde durante cinco días

Ya contado en §6. La lección transferible: **una señal de vivacidad puede ser la propia
avería**, y un healthcheck que sólo mira frescura no distingue «vivo» de «frenético».

### 11.3 Un caso nuevo, verificado hoy: `core-api` responde `not_ready`

`GET /v1/ready` devuelve **503** ahora mismo `[V]`:

```json
{"status":"not_ready","alembic":"core0032","expected":"core0029"}
```

La base está donde debe (`core0032`). Lo que está congelado es el **esperado**:

```python
@lru_cache(maxsize=1)
def _expected_head() -> str:
    """Head de la cadena de migraciones del core (según el código desplegado)."""
```

`lru_cache` calcula el head **una vez por proceso** y lo guarda para siempre. El proceso
arrancó el **2026-08-25T14:42:31Z** `[V]`; `core0030` y `core0031` llegaron el 2026-08-26
00:19 y `core0032` el 2026-08-26 11:33 `[V]`. La caché se pobló cuando el head era
`core0029` y no puede refrescarse sin reiniciar.

Es la misma clase que O-1, con el signo invertido: **aquella estaba verde estando rota; ésta
está roja estando sana**. Hoy no rompe nada porque `core-api` no declara *healthcheck* en el
compose y ningún orquestador actúa sobre esa sonda `[V]`. En un despliegue donde sí se
consulte, sacaría el servicio de rotación sin motivo — y, peor, entrenaría al operador a
ignorar la sonda de readiness.

**No lo he modificado ni he reiniciado nada.** Queda anotado como hallazgo abierto.

> ### ✅ ACTUALIZACIÓN (tarde del 2026-08-27) — cerrado, en dos intentos
>
> `/v1/ready` responde ya
> `{"status":"ready","alembic":"core0032","release":"ae7fbf2","authoritative":true}` `[V]`,
> y `core-api` reporta *healthy*. El detalle de por qué hicieron falta **dos** intentos está
> en §11.4, y merece leerse: es el mejor ejemplo de §12 que tiene el proyecto.

### 11.4 El arreglo que abrió el fallo simétrico (auditoría externa, P1-3)

Los dos fallos son **la misma incoherencia** vista por sus dos caras: la expectativa del
head y el código que responde venían **de sitios distintos**.

| Versión | Qué hacía `_expected_head()` | Fallo |
|---|---|---|
| Antes de `bf3fbfd` | `@lru_cache` sin clave: expectativa fijada para toda la vida del proceso | **Falso ROJO** — §11.3: dos días de 503 con la BD sana |
| `bf3fbfd` (2026-08-26) | releía la cadena **del volumen montado**, en caliente | **Falso VERDE** — el volumen pasa a la release B, `core-migrate` migra a B, `/v1/ready` certifica B… con los módulos ya importados todavía en A |
| `f728518` + `ae7fbf2` (vigente) | `_EXPECTED_HEAD` se lee **una vez al importar**, desde la misma imagen que trae los handlers; si la cadena no se puede leer, el proceso **no arranca** | — |

Lo encontró una **auditoría externa independiente**, no la mía. Y lo importante es que el
falso verde **no lo cierra la lectura, lo cierra el despliegue**: el perfil operativo dejó de
montar `./jobhunt_core`, así que cambiar la cadena exige cambiar la imagen, y eso recrea el
proceso. Mientras el código se monte, ninguna estrategia de lectura es coherente.

Se cerró además la ceguera que §11.1 y §11.2 comparten —un operador no podía distinguir
releases—: `/v1/health` publica ahora `release` (SHA horneado como build arg, **nunca** en
`environment:`, para que no pueda desligarse del código) y `alembic_expected`; `/v1/ready`
añade `release` y `authoritative`. Con SHA + head, «todos los procesos publican lo mismo» es
un paso de verificación **comprobable**, no confiado. `core-api` gana healthcheck de compose
contra `/v1/ready` (⚠ **no** en `docker-compose.prod.yml` ni `.qnap.yml`: siguen sin él).

> **Qué cambia para quien trabaje aquí:** todo comando del core que deba ver el árbol de
> trabajo necesita `-f docker-compose.yml -f docker-compose.dev.yml`. Sin el override se
> prueba el código de la **imagen**. `CORE_CODE_MUTABLE=1` hace que `/v1/ready` conteste
> `authoritative: false`: verde informativo, no autorización para operar.

---

## 12. Qué acreditan los 7 ciclos, y qué no

Esta sección existe porque el gate es la evidencia sobre la que se decide un go/no-go, y una
evidencia que se lee mal es peor que ninguna.

**Lo que la racha NO significa** `[I]`. `dedup_precision`, `dedup_recall` y el término de
huecos de `perdida` son **fotos del estado actual** en el momento del cómputo (06:05), no
mediciones acotadas a la ventana del ciclo. Sobre un corpus **estable**, 7 ciclos verdes
consecutivos acreditan **7 repeticiones de la misma medición**, no 7 jornadas
independientes. La racha demuestra estabilidad del veredicto en el tiempo; la independencia
entre jornadas la aporta el flujo real de datos (la cosecha diaria y el CDC), no la métrica.

**Las métricas que sí tienen ventana real del ciclo** son `outbox_dead` (por su timestamp de
transición), `latencia_p95`, `coste` y `reenlace_pct` `[I]`.

**Otros límites del alcance:**

- El **espejo del criterio de la gracia** es exacto fila a fila, pero **aproximado entre
  filas del mismo lote** `[I]`. La precondición que lo haría exacto es inalcanzable con el
  esquema legacy actual.
- `lease_overrun` **puede contar una fila dos veces** `[I]`. Es un contador de alarma, no
  una métrica de gate — y está etiquetado como tal para que nadie lo use como si lo fuera.
- El oráculo es un **set etiquetado a mano**, y su calidad acota lo que el gate puede
  demostrar. El recall del estrato positivo se publica hoy como **fila informativa**, no
  vinculante `[I]`.
- **Los 19 casos ambiguos del estrato están excluidos definitivamente** `[I]`: para un
  estrato positivo la pureza manda, y un ambiguo etiquetado como duplicado contaminaría el
  numerador del recall.

---

## 13. Vías muertas: cambios que se midieron y dejaron el sistema peor

Este proyecto tiene dos casos documentados en los que la «mejora» era una regresión. Están
aquí para que no se repitan.

### 13.1 «Un candidato con divisa y fuera de paréntesis gana a `plain`»

En el parser de salarios, esta regla **recupera las nueve formas** que fallaban y es neutra
sobre el corpus vivo. Parece una mejora estricta. **Rompe un caso ya fijado en la suite**
`[V]`:

```
Salaire annuel 90 000 - 110 000, soit 7 500 - CHF 9 200 par mois   →   (7500, 9200)
```

La glosa no siempre va entre paréntesis. **No repetirla.**

### 13.2 Bajar el umbral del dedup para recuperar pares cross-idioma

Ya desarrollado en §4.3: la separación está **invertida** (falso positivo 0,8220 > duplicado
real 0,8195), la rama era inalcanzable de todos modos, y el precio de equivocarse son
vacantes reales desactivadas. El commit que la introdujo lo documentaba en su propio
mensaje: «baja el umbral».

### 13.3 Dos casos donde la medición contradijo al informe, y ganó la medición

- **El barrido de dedup.** La auditoría cifraba el bucle en «14-30 s» y estimaba «2-5 s»
  después. Medido: **324,6 s antes y 307,5 s después** (−5,3 %) `[I]`. El bucle de búsqueda
  aproximada cuesta **un orden de magnitud más** de lo que decía el informe, y el transporte
  no era su parte cara. Se aplicó igualmente porque los 78 MB de socket y los 39 MB de RAM
  del worker son reales y el cambio son dos líneas de SQL — pero la justificación cambió.
- **Las cifras del WAL.** El mensaje del commit de consolidación `fbe22f0` dice «1.083
  escrituras/s, 42 GB de WAL al día». El informe de optimización midió **559–979
  actualizaciones/s** (48,3 M/día) y **228 GB/día**, con **dos instrumentos independientes
  que coinciden dentro del 0,6 %** `[I]`. **Las cifras válidas son las del informe.** El
  mensaje del commit es inmutable y seguirá diciendo lo contrario para siempre: es el
  ejemplar más puro de la regla de oro que hay en el repositorio.

### 13.4 Cambios que no se hicieron, con su número

- **Paralelizar los 15 scrapers**: 321 s → ~140 s (2,3×), no aplicado — cambia la huella
  `[I]`.
- **Quitar `NullPool` de la suite**: ahorraría ~150 s más **pero rompe 85 de 85 tests**
  `[I]`.
- **Agrupar los INSERT del dedup del core**: no se hace porque el `rowcount` de un
  `executemany` **no es fiable** para el contador que el gate lee, y la ida y vuelta cuesta
  0,064 ms `[I]`.
- **Refactorizar el código de cutover de un solo uso** (dos funciones con complejidad
  ciclomática 66 y 41): «con la maniobra de migración pendiente, tocarlos ahora es
  exactamente lo que no hay que hacer. Anotar y dejar» `[I]`.
  **La premisa caducó el 2026-08-27**: la maniobra ya se ejecutó. La decisión de no
  refactorizar habrá que **re-justificarla por su propio mérito** (código de un solo uso,
  ya gastado), no por el bloqueo, que ha desaparecido.

---

## 14. Trampas de instrumento

Errores de medición que costaron conclusiones falsas. Valen más que muchos hallazgos.

1. **`pg_stat_*` desde una misma transacción miente** `[I]`. Con
   `stats_fetch_consistency = cache`, dentro de una transacción se sirve una instantánea
   cacheada. La primera medición de la auditoría de optimización dio «0 escrituras» por eso.
   **Hay que muestrear desde conexiones distintas.**
2. **`docker compose ps` da la antigüedad de creación, no la del proceso** `[V]`. Hoy dice
   «4 weeks ago» para un `core-capture` cuyo proceso lleva 9 horas. Y `RestartCount` sólo
   cuenta reinicios de la política: un `stop`+`start` manual lo deja en 0. Lo correcto es
   `docker inspect -f '{{.State.StartedAt}}'` contra la fecha del commit.
3. **`grep` al fichero no responde qué código corre** `[I]`. Python importa cada módulo una
   vez. Un fichero corregido dentro de un contenedor cuyo proceso arrancó antes es
   exactamente la avería O-1.
4. **Dos `pytest` concurrentes se pisan** `[I]`. El teardown hace `TRUNCATE ... CASCADE` de
   todas las tablas de la base de test: dos corridas simultáneas producen deadlocks y rojos
   falsos. Un rojo de 8 fallos + 2 errores se cerró como artefacto de concurrencia.
5. **Un test que lee la cota del módulo que audita no mide nada** `[I]`. Toda guarda de cota
   debe afirmar el **número literal** y probarse por **mutación**.

**Protocolo de sondas** que las auditorías siguieron y conviene mantener `[I]`: escrituras
**sólo** contra la base de test, en transacción revertida; contra producción **sólo
`SELECT`** y `EXPLAIN (ANALYZE, BUFFERS)` sobre `SELECT`; claves Redis con prefijo y
borradas al terminar; **ningún servicio reiniciado, recreado ni reconstruido**; árbol del
repositorio intacto. Es el protocolo que he seguido al escribir estos tres documentos.

---

## 15. Qué mirar primero cuando algo va mal

| Síntoma | Primera hipótesis | Cómo confirmarla |
|---|---|---|
| Los títulos no se traducen | `GROQ_API_KEY` caducada o revocada | `HTTP 401` en el log del backend |
| El gate no avanza pese a métricas verdes | No hay cohorte de dedup congelada | `SELECT * FROM jobhunt.labeled_dedup_cohorts` — si está vacía, ningún ciclo es elegible |
| Un scraper devuelve 0 ofertas sin error | Bloqueo blando, o falta su fila de cumplimiento | Buscar el marcador anti-bot; `SELECT ... FROM source_compliance` |
| El WAL crece sin control | Consumidor CDC caído o atascado | `pg_replication_slots`: `active`, y la distancia a `restart_lsn` |
| Un healthcheck lleva días verde y algo no cuadra | Proceso ejecutando código anterior a su arreglo | `docker inspect ... StartedAt` contra `git log -1 --format=%cI -- <fichero>` |
| Un salario absurdo (×1000, ×2000) | Un scraper prellenó `salary_*_chf` con divisa no-CHF | Comparar `salary_original` con `salary_min_chf` |
| Ofertas que desaparecen sin motivo | Deriva de identidad legacy, o dedup marcando duplicados | `duplicate_of IS NOT NULL`; y revisar `last_seen_at` |

---

## Continuar

- **[DOC 1 — Blueprint](DOC_1_BLUEPRINT_2026-08-27.md)** — el problema, las decisiones, el
  estado y lo que está a medias.
- **[DOC 2 — Componentes](DOC_2_COMPONENTES_2026-08-27.md)** — el inventario pieza a pieza.
- **`SwissJob/docs/COTAS_Y_DECISIONES.md`** — el índice largo de cotas aceptadas, con
  puntero a la versión extensa de cada una. ~~**Aviso de lectura:** su §8 («afirmaciones
  falsas vivas») estaba obsoleta en 5 de sus 6 entradas al verificarla el 2026-08-27~~ →
  **§8 fue reescrita** la tarde del 2026-08-27 con las cinco cerradas y la única que no
  puede cerrarse (un mensaje de commit es inmutable). Su **§9** —estado operativo— también
  se reescribió entera, y **§9.1** cuenta el caso `bf3fbfd` de §11.4.
- **`SwissJob/jobhunt_core/shadow/RUNBOOK.md`** — la operación ejecutable de la sombra y el
  **acta** de la maniobra de canonización, ya ejecutada (su §7, con las verificaciones
  posteriores y el procedimiento para repetirla en el NAS).
- **`/home/lothar/Public/AUDITORIA_EXTERNA_BUGS_2026-08-27.md`** y
  **`AUDITORIA_EXTERNA_DISENO_2026-08-27.md`** — la auditoría externa independiente del
  2026-08-27: veredicto **NO-GO con cinco condiciones**, y los hallazgos P1-1/P1-2/P1-3/P2-1
  que motivaron los arreglos de esa tarde.
