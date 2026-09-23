# Diagnóstico — «el panel de ofertas no muestra casi nada» (2026-09-23)

Síntoma reportado por el propietario: títulos en idioma original, sin resumen
de la oferta, sin forma de entender de qué va; en colegios, «mandar email» sin
descripción. Se pedía: título comprensible, un par de frases sobre la oferta,
y poder decidir «me interesa / aplicar / guardar» desde el panel.

Medido en producción el 23-09 (sólo lectura): feed servido de 1.800 ofertas
(`/match/results?limit=3000`), 200 del catálogo (`/jobs/search`), canónica del
core, raws de las fuentes, caché de Redis y detalle (`/jobs/{hash}`).

## 1. Lo que la tarjeta muestra hoy, y por qué se ve vacía

`MatchCard` (pantalla `/match`) ya tiene: título (enlace al detalle), empresa,
localización, **descripción de hasta 3 líneas si existe**, skills, explicación
LLM, y cuatro acciones: **Good** (thumbs up, «Save match»), **Not for me**,
**Apply** (externo) y **View details** (`/job/{hash}`). La tarjeta de colegios
(`WatchlistPage`) tiene título, política, estado de candidatura, borrador y
enlace externo; **no tiene descripción ni «View details»**.

Es decir: los botones existen; lo que no existe es el **texto**. Tres causas,
por orden de peso:

### 1.1 El 28,5 % de la pantalla principal no tiene descripción — en ningún sitio

| Fuente | Ofertas en el feed | Sin descripción | Por qué |
|---|---:|---:|---|
| `jobgether` | 491 | **491** | El endpoint de búsqueda no la trae. `native_jobgether.py:76` y el legacy `providers/jobgether.py:141` escriben `"description": ""` **a propósito**. La descripción está en la página de la oferta, que nadie descarga |
| `financejobs` | 10 | **10** | El raw de la lista trae las claves `description` y `summary`… **a `null`** (verificado). Están sólo en el detalle (`jobId`, `pdfUrl`) |
| `publicjobs` (legacy) | 5 | 5 | idem |
| colegios (`swiss_schools_*`) | 7 | **7** | El colector escolar captura título, URL y política; nunca la descripción |
| **Total** | 1.800 | **513 (28,5 %)** | |

Para estas ofertas el detalle (`/job/{hash}`) también sale vacío (`desc len 0`
en las tres escolares y en la nativa probadas): no es que la tarjeta lo
esconda, es que **no hay nada que mostrar**. Un resumen de dos frases es
imposible sin texto de origen.

### 1.2 Otro 5,7 % muestra HTML crudo (regresión del punto 4)

`arbeitnow` nativo: **111/111** revisiones canónicas con HTML (`<h1><strong>…`).
`jobhunt_core/harvest/providers/arbeitnow.py:93` pasa `raw["description"]` tal
cual; el legacy (`backend/providers/arbeitnow.py:78`) lo limpiaba con
`strip_html_tags`. La paridad del traspaso comparó identidad y cabeceras, no
la forma del texto. En el feed: 103 tarjetas cuyo «resumen» son etiquetas.
`native_json.py` sí limpia (`_plain`), pero arbeitnow no pasa por ahí.

### 1.3 Los títulos no se traducen en la pantalla principal — nunca

`MatchPage` pide `translate=false` (decisión de coste: 3.000 ofertas). La
traducción existe (`TranslationService`, LLM Groq, caché Redis 30 días) pero
sólo la piden `/match/saved` y `/history`. **La caché tiene 7 títulos.**
Activar la traducción en la pantalla principal tal cual serían ~1.540 llamadas
al LLM en la primera carga (free tier 8k tokens/min): no es una opción síncrona.
El catálogo (`/jobs/search`) ni siquiera tiene campo de título traducido.

## 2. Lo que NO es el problema

- La tarjeta no está «casi vacía» por diseño: con descripción presente muestra
  3 líneas, skills y explicación.
- «No hay botón me interesa»: es **Good** (pulgar arriba, título «Save match»).
  Es un problema de etiqueta, no de función — se puede renombrar.
- «No puedo entrar en la oferta salvo por Aplicar»: **View details** existe en
  `MatchCard`; en la tarjeta de colegios no. Y entrar en una oferta sin
  descripción muestra… una página sin descripción.

## 3. Qué haría, por orden, y qué decide el propietario

**A. Sin decisión — corregir lo que está roto (medio día):**
1. `arbeitnow` nativo: limpiar HTML en el normalizador (misma función que el
   legacy), con prueba de paridad texto-legacy/texto-nativo. Las 111
   revisiones existentes se re-normalizan en la siguiente cosecha (nuevo
   `content_hash`) — o con un barrido único.
2. Tarjeta de colegios: añadir descripción (cuando exista) y «View details»,
   como en `MatchCard`. Renombrar **Good → Me interesa** y **Save match** de
   forma coherente con «guardar» (hoy «guardar» y «me interesa» son el mismo
   pulgar).
3. `JobDetailPage`: cuando no hay descripción, decirlo («esta fuente no
   publica la descripción; ábrela en el portal») en vez de un hueco.

**B. Decisión 1 — descripciones para el 28,5 % (jobgether, financejobs, colegios):**
única vía: **descargar la página de cada oferta** en la cosecha nativa
(jobgether: página de oferta; financejobs: detalle por `jobId`; colegios: la
URL de la vacante). Coste: una petición más por oferta nueva (jobgether ≈ 500
páginas en la primera pasada, luego sólo las nuevas), con el ritmo y las
cabeceras que ya usa cada productor. Es exactamente el tipo de tráfico que el
proyecto ha acotado con cuidado (`COTAS` §4): hay que decidirlo, no colarlo.

**C. Decisión 2 — el resumen de «una o dos frases»:** no existe. Sería un
resumen LLM por oferta, calculado **una vez por `text_hash`** en segundo plano
(mismo patrón que el idioma persistido: tabla, tarea acotada, servir sólo
lee), nunca en la petición. Coste: ~1.300 ofertas con texto hoy × una llamada,
después sólo las nuevas (~50–150/día). Cabe en Gemini Flash; en Groq free tier
habría que dosificarlo. Alternativa sin LLM y sin coste: mostrar las primeras
~300 letras **limpias** de la descripción (hoy ya se hace, y con HTML limpio
es un resumen aceptable para el 66 % que tiene texto).

**D. Decisión 3 — títulos comprensibles en la pantalla principal:** misma
arquitectura que C: traducir **en segundo plano** por título (la caché Redis
existente, calentada por una tarea acotada; el router sólo lee). Un título
nuevo aparece en original durante minutos y luego traducido. Coste: ~1.540
llamadas la primera vez, después sólo títulos nuevos.

Mi recomendación: **A ahora**; **C sin LLM + D** (barato, resuelve el 66 % y
los títulos); y **B** sólo para jobgether, que es el 27 % del feed y donde una
petición por oferta nueva es asumible — antes de gastar un LLM en resumir
ofertas que no tienen texto.

## 4. Datos crudos de la medición

- Feed 1.800: sin descripción 513; con HTML 103; `job_title_en` 0; `school_id` 7.
- Catálogo 200: `financejobs` 26/26 y `publicjobs` 9/9 sin snippet; `arbeitnow` 115/139 con HTML.
- Canónica del feed: `legacy:arbeitnow` 0/836 con HTML, `arbeitnow` nativo 111/111; `legacy:jobgether` 466/466 vacías, `jobgether` 25/25; `financejobs` 9/9.
- Raw financejobs: `description` y `summary` presentes y `null`.
- Redis: 7 claves `translate:title:*`.
- Detalle `/jobs/{hash}`: 200 con descripción vacía en la nativa y en 3/3 escolares probadas.
