# DOC 2 — Componentes: el recorrido pieza a pieza

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


> **Nivel 2 de 3.** Un inventario razonado: qué es cada pieza, para qué está, qué
> responsabilidad tiene y con quién habla. El *por qué* arquitectónico está en
> [DOC 1 — Blueprint](DOC_1_BLUEPRINT_2026-08-27.md); los mecanismos internos están en
> [DOC 3 — Detalle técnico](DOC_3_DETALLE_TECNICO_2026-08-27.md).
>
> **Fecha:** 2026-08-27 (mañana). `[V]` = verificado por mí leyendo código o ejecutando;
> `[I]` = procedente de informe o de mapeo asistido, no re-medido.
>
> ⚠ **ACTUALIZADO la tarde del 2026-08-27**: se ejecutaron las dos maniobras pendientes, se
> arregló el `not_ready` de `/v1/ready` y el perfil operativo del core pasó a **imagen
> inmutable** (los comandos del core sobre el árbol de trabajo necesitan ahora
> `-f docker-compose.yml -f docker-compose.dev.yml`). Estado consolidado:
> `ESTADO_Y_HOJA_DE_RUTA.md` **§20**.

---

## 0. Mapa de despliegue

Nueve contenedores en el stack de SwissJob, tres en el del portfolio. Todos corriendo al
escribir esto `[V]`.

> **Nota de perfil (2026-08-27, tarde).** Los cuatro servicios del core —`core-api`,
> `core-worker`, `core-capture` y `core-migrate`— dejaron de montar `./jobhunt_core` en el
> compose base: corren el código de **la imagen**, con `RELEASE_SHA` horneado como build arg.
> `core-api` ganó healthcheck contra `/v1/ready`. El bind mount vive ahora en
> `docker-compose.dev.yml`, un override **explícito** que hay que pedir con los dos `-f`.

```
┌─ stack swissjob ───────────────────────────────────────────────────┐
│                                                                    │
│  backend ──────┐                              ┌── core-api :8003   │
│  (FastAPI      │                              │   (FastAPI /v1)    │
│   :8002)       │        postgres :5435        │                    │
│                ├────────  swissjobhunter ─────┤                    │
│  worker        │        esquema public        │   core-worker      │
│  worker-ai ────┤        esquema jobhunt       │   (Celery + beat)  │
│  (Celery)      │              ▲               │                    │
│      │         │              │ slot lógico   │        │           │
│      │         │              │ wal2json      │        │           │
│  redis :6380 ──┘         core-capture ────────┘   redis-core :6381 │
│  (caché+broker)          (consumidor CDC)         (broker core)    │
└────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │ HTTP /v1 (Bearer)
┌─ stack portfolio ──────────────────┼───────────────────────────────┐
│  portfolio_backend ────────────────┘   portfolio_db   portfolio_redis│
└─────────────────────────────────────────────────────────────────────┘
```

Puertos host `[V]`: PostgreSQL **5435**, Redis **6380**, Redis core **6381** (loopback),
backend legacy **8002**, core API **8003**, frontend **5174**. Están así para no chocar
con servicios preexistentes del host (5433, 5434, 6379, 5678, 8001).

**Los dos schedulers no son lo mismo.** El legacy usa **APScheduler**
(`services/scheduler.py`), que sólo *despacha* a Celery y elige líder por Redis. El core
usa el **beat embebido de Celery** (`worker … -B`), con 9 cadencias `[V]`. Confundirlos
lleva a buscar un `beat_schedule` en el backend legacy, que no existe `[V]`.

---

## 1. SwissJob — backend legacy (`/home/lothar/Public/SwissJob/backend/`)

Es a la vez el **motor actual** y el **BFF en construcción**. Hoy manda el motor.

> **Nota de exactitud:** el `CLAUDE.md` del repositorio describe un directorio `api/`. No
> existe `[V]`; los routers viven en `backend/routers/`.

### 1.1 Superficie HTTP — `routers/` (10 routers, prefijo `/api/v1`) `[I]`

| Router | Recurso | Habla con |
|---|---|---|
| `auth.py` | Registro, login, JWT | `core/security.py`, `models/user.py` |
| `jobs.py` | Catálogo, búsqueda, detalle, estadísticas, fuentes | costura `services/catalog/` |
| `match.py` | Pipeline de matching, resultados, feedback | costura `services/matching/`, `match_service` |
| `profile.py` | Perfil de usuario, subida y análisis de CV | `cv_parser`, `cv_analyzer`, costura `profiles/` |
| `applications.py` | Candidaturas y su estado | costura `services/applications/` |
| `documents.py` | CV y cartas generadas | costura `services/documents/` |
| `saved_searches.py` | Búsquedas guardadas y su ejecución | `tasks/search_tasks.py` |
| `watchlist.py` | Colegios vigilados, salud, digest | costura `services/schools/` |
| `notifications.py` | Bandeja + stream SSE | `sse_manager` |
| `analytics.py` | Patrones de rechazo, métricas | `pattern_analysis_service` |

Cada router tiene su gemelo Pydantic en `schemas/`. Los routers no consultan la base
directamente: parsean, delegan y responden.

### 1.2 Adquisición — `providers/` y `scrapers/`

**Providers (25 registrados, 16 instanciables sin credenciales)** `[V]`. Un provider
consume una API o un feed. Todos heredan de `BaseJobProvider` y exponen tres cosas:
`get_source_name()`, `fetch_jobs()`, `normalize_job()`. Cada uno lleva su propio
`CircuitBreaker`.

Familias `[V]`:
- *Agregadores generales* (5): `adzuna`, `arbeitnow`, `careerjet`, `jooble`, `jsearch`.
- *Remoto generalista* (6): `remotive`, `weworkremotely`, `workingnomads`, `euremotejobs`,
  `remoteco`, `jobspresso`.
- *Remoto europeo/nórdico* (3): `thehub`, `jobgether`, `nav_arbeidsplassen`.
- *Lingüístico* (1): `proz`.
- *Suizos* (4): `ostjob`, `zentraljob`, `publicjobs`, `zebis`.
- *Organismos internacionales* (1): `globaljobs`.
- *Restringidos, sólo por ruta autorizada* (5): `jobcloud_partner`, `linkedin_authorized`,
  `indeed_partner`, `glassdoor_partner`, `xing_partner`.

Nueve requieren clave (4 públicos con clave + los 5 restringidos); sin ella, el registro
**no los instancia**: cero peticiones. Los 5 restringidos no tienen ruta de scraping
público en absoluto.

**Scrapers (15 registrados)** `[V]`. Un scraper extrae de HTML. `BaseScraper` extiende
`BaseJobProvider`, de modo que reutiliza *circuit breaker*, normalización y estadísticas, y
añade lo suyo: límite de ritmo con *jitter*, reintentos con retroceso exponencial,
detección de bloqueo blando, pre-comprobación de cumplimiento, y dos modos de obtención de
HTML (httpx+BeautifulSoup para SSR, Playwright endurecido para SPA).

- *Base* (7): `gastrojob`, `stelle_admin`, `tes`, `schuljobs`, `myscience`, `financejobs`,
  `irishjobs`.
- *Watchlist de colegios internacionales* (8): `swiss_schools_{nae,isp,inspired,zis,isb,ecolint,hautlac,iscs}`.

`medjobs` existe pero está fuera del registro: Cloudflare le impone un desafío duro que el
Playwright endurecido local no supera `[I]`.

### 1.3 Servicios — `services/`

**Pipeline de datos**

| Módulo | Responsabilidad |
|---|---|
| `job_service.py` | `BaseJobProvider`: la abstracción de la que heredan providers **y** scrapers |
| `scraper_engine.py` | `BaseScraper`: motor común de los 15 scrapers |
| `data_normalizer.py` | Enriquece el dict crudo: salario a CHF, idioma, seniority, tipo de contrato, saneo de enums |
| `job_classifier.py` | Clasifica en las 13 categorías A–H del perfil; se ejecuta dentro de la normalización |
| `job_repository.py` | Upsert e integración con dedup; define `JobIdentityConflictError` |
| `deduplicator.py` | Los tres niveles de deduplicación y el hash difuso |
| `harvest_window.py` | Decide qué entra al corpus según fuente y `published_at`; vigila la deriva de identidad |
| `job_matcher.py` | Embeddings multilingües (singleton perezoso) y puntuación multifactor |
| `match_service.py` | Orquesta el pipeline de matching de tres etapas |
| `match_result_service.py` | Lectura y CRUD de resultados por usuario; separado de `match_service` por responsabilidad única |

**IA y lenguaje**

| Módulo | Responsabilidad |
|---|---|
| `groq_service.py` | Re-ranking y chat vía Groq; SDK síncrono envuelto en *threadpool*, caché Redis |
| `gemini_service.py` | Cliente httpx mínimo sobre Gemini; interfaz intercambiable con Groq |
| `translation_service.py` | Traduce títulos DE/FR/IT → EN por lotes, caché Redis 30 días |
| `document_generator.py` | CV y carta en Markdown: Gemini primario, Groq de respaldo |
| `cv_analyzer.py` / `cv_parser.py` | Extrae campos estructurados del CV / extrae texto de PDF y DOCX |
| `letter_generator.py` | Borrador de carta con plantillas A/B y plantilla de respaldo si el LLM cae |
| `skill_synonyms.py` | Elimina «competencias que faltan» que son falsas por sinonimia |
| `pattern_analysis_service.py` | Analiza rechazos y sugiere patrones de exclusión |

**Cosecha responsable y salud de fuentes**

| Módulo | Responsabilidad |
|---|---|
| `compliance.py` | `ComplianceEngine`: verifica antes de cosechar; *kill-switch* tras 3 bloqueos |
| `crawler_budget.py` | Presupuesto de páginas por run — decisiones **puras, sin E/S** |
| `cursor_store.py` | Cursor incremental por fuente y ámbito; ventana de URLs recientes para el corte temprano |
| `scraper_stealth.py` | Utilidades **puras**: cabeceras realistas, retardo con *jitter*, detección de bloqueo blando |
| `circuit_breaker.py` | CLOSED → OPEN → HALF_OPEN → CLOSED, uno por fuente |
| `source_health.py` | Traduce el resultado de un run a una fila de `source_health` y decide si alertar |

**Notificación y entrega**

`email_service.py` (SMTP con la biblioteca estándar, **síncrono a propósito** porque lo
llaman tareas Celery `def`), `teacher_alert.py` (detecta plaza de profesor de primaria
reutilizando la categoría H del clasificador), `daily_digest.py` (función **pura**: recibe
ofertas, devuelve asunto/texto/HTML, escapa contra inyección), `urgency_scorer.py` (realce
0–100 que se suma **aparte** del `score_final` para no contaminar la base del matching),
`sse_manager.py` (SSE sobre Redis pub/sub para difusión entre workers).

**Orquestación y costura**

`scheduler.py` (APScheduler + elección de líder en Redis) y `routing.py` + los seis
paquetes de capacidad. Ver §1.6.

### 1.4 Tareas Celery — `tasks/`

19 tareas registradas en tres colas: `ai`, `scraping` y `default` `[I]`. Configuración de
seguridad: `task_acks_late=True`, `worker_prefetch_multiplier=1`, límites de tiempo blando
300 s / duro 360 s.

La pieza central es **`pipeline_tasks.py` — la cosecha diaria autónoma**: una cadena Celery
secuencial

```
fetch_providers → fetch_scrapers → embed_all_pending → dedup_semantic_batch → [run_all_matches]
```

despachada **una vez al día a hora variable** (jitter, patrón circadiano). La última etapa
es **condicional**: se omite entera si ningún perfil activo pertenece al legacy — es el
gate anti-doble-motor de la Fase D. La cadena se despacha con `link_error` para dejar
rastro si un eslabón cae.

Las demás: `fetch_tasks`, `scraping_tasks`, `embedding_tasks`, `matching_tasks`,
`maintenance_tasks` (dedup por lotes, comprobación de URLs, limpieza de ofertas rancias),
`search_tasks`, `watchlist_tasks`, `alert_tasks`, `digest_tasks`, y `watermarks.py`, un
juego de ayudantes anti-reenvío sobre Redis compartido por alertas y digests.

**Cadencias de APScheduler** `[I]`, todas en `Europe/Zurich` y gobernadas por
`SCHEDULER_ENABLED`: cosecha diaria (con jitter), dedup semántico 04:00, comprobación de
URLs 03:00 (diaria — **no** semanal), limpieza 03:30, búsquedas guardadas por intervalo,
salud de watchlist cada 6 h, digest de watchlist 18:00, alerta de profesor por intervalo,
digest diario opcional. Si la cosecha diaria está activa —lo está `[V]`—, la cosecha por
intervalos de 6 h **no se registra**.

### 1.5 Modelos — `models/` (14 tablas + enums) `[I]`

`jobs` (corpus con vector pgvector), `users`, `user_profiles`, `match_results`,
`job_applications`, `generated_documents`, `saved_searches`, `notifications`,
`job_filters`, `pattern_suggestions`, `source_compliance`, `source_health`,
`source_cursors`, y las dos de la costura: `jobhunt_routing` y `jobhunt_profile_map`.

`jobhunt_profile_map` traduce `users.id` legacy a UUID de perfil del core, y vive **local
al BFF** porque el `/v1` no expone búsqueda por `external_ref` y la frontera prohíbe leer
el esquema del core directamente.

### 1.6 La costura de SwissJob — `services/routing.py` + seis paquetes

`routing.py` resuelve, para cada `(consumer, perfil, capacidad)`, qué implementación sirve.
Precedencia: fila exacta → fila **comodín** del consumer → `local` por defecto. Caché en
proceso con invalidación **transaccional** (sólo tras confirmar el commit).

Seis capacidades `[V]`: `catalog`, `matching`, `profiles`, `applications`, `documents`,
`schools`. Cada una es un paquete con la misma estructura de cinco ficheros:

| Fichero | Qué es |
|---|---|
| `port.py` | El contrato: la interfaz que ambas implementaciones cumplen |
| `local.py` | La implementación del motor actual |
| `core_client.py` | El cliente HTTP contra `/v1` |
| `seam.py` | El selector: traduce modo → implementación, y envuelve el fallback |
| `__init__.py` | Fachada del paquete |

**Dos familias de capacidad, y la diferencia importa** `[V]`:

- `catalog` / `matching` / `profiles` — el `/v1` sirve la lectura, hay canary real. En
  `core_read` se usa un envoltorio con **fallback al local**; en `core_primary` el cliente
  del core **sin fallback silencioso**.
- `applications` / `documents` / `schools` — variante ligera: el `/v1` no expone la
  capacidad (cota fijada por *contract test*) y su único escritor es local ⇒ se sirven de
  local **en todos los modos**.

`LEGACY_OWNED_MODES = (local, shadow)` `[V]` es el gate anti-doble-motor: sólo en esos dos
modos actúan los schedulers del legacy. En `rollback_pending` el core **sigue** siendo el
escritor hasta el replay final; actuar ahí duplicaría matching y correo al usuario.

**Estado hoy: la tabla `jobhunt_routing` de SwissJob está vacía** `[V]` ⇒ todo resuelve a
`local`. La costura está construida y probada, no activada.

---

## 2. `jobhunt_core` — el núcleo (`/home/lothar/Public/SwissJob/jobhunt_core/`)

Servicio desplegable con esquema Postgres propio (`jobhunt`), 46 tablas `[I]`, y una cadena
de migraciones escrita a mano — **no hay modelos declarativos**: el esquema vive
íntegramente en las migraciones `[I]`.

### 2.1 Fundamentos

| Módulo | Responsabilidad |
|---|---|
| `config.py` | Settings **aisladas**: sólo variables `CORE_*`, sin `env_file`. En `CORE_ENV=prod` exige rol `jobhunt_core`, esquema `jobhunt`, broker en `redis-core`, y rechaza secretos de relleno |
| `database.py` | Motor, sesión y `Base` declarativa, siempre dentro del esquema propio |
| `migrate.py` | Job de migración de un disparo, en contenedor propio (`core-migrate`). Arranca roles idempotentemente y **verifica el aislamiento** y pgvector antes de dar por buena la migración. ⚠ Desde `ae7fbf2` **no monta** `./jobhunt_core`: corre el código de la imagen. Para ejecutarlo contra el árbol de trabajo (tests, migraciones nuevas) hay que añadir `-f docker-compose.yml -f docker-compose.dev.yml` |
| `celery_app.py` | App Celery: broker dedicado, colas `core.*`, rutas, y el `beat_schedule` de 9 cadencias |
| `credentials.py` | Credenciales de consumidor: token `Bearer <key_id>.<secret>`, sha256 en base, comparación en tiempo constante, scopes en JSONB |

### 2.2 API `/v1` — `api/`

Tres routers más dos sondas `[I]`, verificados vivos `[V]`:

- `GET /v1/health` — liveness pura. Devuelve `{"status":"ok",...}` `[V]`, y desde
  `f728518` publica además `release` (el SHA horneado en la imagen) y `alembic_expected`:
  con esas dos señales, verificar un despliegue es comprobable en vez de confiado.
- `GET /v1/ready` — readiness: la base responde **y** está migrada al head esperado.
  ~~**Hoy devuelve 503** por una caché congelada~~ → ✅ **arreglado y desplegado la tarde
  del 2026-08-27** (`f728518` + `ae7fbf2`): responde
  `{"status":"ready","alembic":"core0032","release":"ae7fbf2","authoritative":true}` `[V]`.
  `authoritative` es `false` en el perfil de desarrollo (código montado): verde
  **informativo**, no autorización para operar. El historial completo —incluido que el
  primer arreglo cerró un falso rojo y abrió el falso verde opuesto— en DOC 1 §5 y DOC 3 §11.

| Router | Endpoints |
|---|---|
| `v1.py` | `GET /v1/vacancies` (keyset, filtros, ETag), `GET /v1/vacancies/{id}`, `GET|PUT /v1/profiles/{id}`, `GET /v1/profiles/{id}/matches` |
| `v1_applications.py` | `GET|POST /v1/applications`, `PATCH|DELETE /v1/applications/{id}`, `PUT /v1/profiles/{id}/bookmarks` |
| `v1_saved_searches.py` | `GET|POST /v1/saved-searches`, `PUT|DELETE /v1/saved-searches/{id}` |

Piezas de apoyo: `deps.py` (dependencias, `HTTPBearer` con `auto_error=False`, `Principal`,
`require_scope`, validadores de repertorio), `schemas.py` (los DTO del contrato),
`idempotency.py` (clave natural `(consumer, key, route)`, TTL 24 h).

El corpus (`/v1/vacancies`) es la **única superficie global sin propietario**; todo lo demás
va por tenant, y un acceso cruzado devuelve **404 indistinguible** de inexistente.

### 2.3 Cosecha — `harvest/`

| Módulo | Responsabilidad |
|---|---|
| `types.py` | `RawListing`, `FetchResult`, `ScopeRunResult` |
| `provider.py` | Contratos: `BaseProvider` (ABC) y `ListingSink` (Protocol) |
| `runner.py` | Ejecuta un ámbito: orden inviolable fetch → sink → commit del cursor **en la misma transacción**, con bloqueo y *fencing* por token de claim |
| `sink.py` | El sumidero real: slot + encarnación + revisión raw + re-enlace determinista por lotes |
| `identity.py` | Identidad determinista, tokens, y el **guard de reciclado** |
| `normalize.py` | Normalización canónica: registro de normalizadores por fuente y coerción central |
| `registry.py` | Alta en caliente de manejadores por espacio de nombres |
| `providers/arbeitnow.py` | El único provider nativo del core, Tier 0 (API pública) |
| `providers/legacy_shadow.py` | Manejador genérico de las fuentes sombra `legacy:<source>` — **no se cosechan**, sólo se proyectan |

### 2.4 Cómputo y corpus

| Módulo | Responsabilidad |
|---|---|
| `embeddings.py` | Vectores de oferta y perfil por `text_hash`, tabla **particionada por modelo** |
| `embedding_recipes.py` | Recetas versionadas de preprocesado: definen qué espacio vectorial es comparable con cuál |
| `matching.py` | Evaluaciones **append-only** con `eval_key` determinista; feed, descartar, guardar |
| `dedup.py` | Generador de candidatos: kNN vectorial + respaldo léxico `pg_trgm` |
| `archive.py` | El barrido de archivado: la **salida** del corpus |
| `retention.py` | Poda de las cuatro tablas de trabajo terminado (outbox, entregas, inbox sombra, evaluaciones) |
| `profiles.py` | Perfiles multi-tenant con revisiones inmutables versionadas por hash |
| `applications.py` | Candidaturas y marcadores: vínculo a vacante, cadena de fusión, eventos de estado |
| `saved_searches.py` | Búsquedas guardadas: separa campos escribibles por el cliente de los del motor |
| `runs.py` | Runs de cosecha idempotentes con id determinista y lease por ámbito con latido |

### 2.5 Entrega — `outbox.py` + `delivery.py`

`outbox.py` es el **único** ayudante de emisión: `event_id` determinista (uuid5), inserción
**en la misma transacción** que la escritura de negocio, `ON CONFLICT DO NOTHING`, y una
fila de entrega por destino.

`delivery.py` despacha *at-least-once* por destino: claim con `FOR UPDATE SKIP LOCKED` +
lease renovable, retroceso exponencial, y **dos** dead-letters distintos. Constantes
verificadas `[V]`: `MAX_ATTEMPTS = 8`, `MAX_CLAIMS_WITHOUT_RESULT = 25`.

### 2.6 Migración del portfolio — la familia `import_portfolio_*` (8 módulos)

Un cutover de datos no es un script: es un procedimiento con procedencia y vuelta atrás
`[I]`.

| Módulo | Papel |
|---|---|
| `import_portfolio.py` | Parte 1: sintetiza vacantes-sombra bajo la fuente `portfolio-import` |
| `import_portfolio_ledger.py` | Libro por **entrada**: creada / reutilizada / en cuarentena, con su razón |
| `import_portfolio_durables.py` | Parte 2: mapea los durables a las tablas de seguimiento del core |
| `import_portfolio_migrate.py` | Parte 3: orquesta 1+2 por usuario y produce la reconciliación |
| `import_portfolio_provenance.py` | Qué filas insertó **este** run, distinguiendo reutilizaciones |
| `import_portfolio_verify.py` | Verificación estructural independiente usando el libro como contrato |
| `import_portfolio_rollback.py` | Vuelta atrás segura respecto a claves foráneas, hijo → padre |
| `import_portfolio_manifest.py` | Reconciliación contra el **origen**, no sólo checksums del destino |

### 2.7 La sombra — `shadow/`

| Módulo | Responsabilidad |
|---|---|
| `capture.py` | Consumidor CDC legacy→core. **Proceso dedicado** (`core-capture`), no una tarea Celery. Bootstrap, streaming `wal2json`, y un healthcheck de ocho señales |
| `projector.py` | Consume el staging en orden `(lsn, seq_in_tx)` por lotes y lo traduce al lenguaje del core **sin abrir rutas de escritura nuevas** |
| `metrics.py` | El catálogo de métricas por ciclo y sus umbrales |
| `gate.py` | El arnés del GATE-SOMBRA: orquesta el ciclo diario, cuenta la racha, vigila el slot, y aloja el rollback/replay y el *drop* de emergencia |
| `labels.py` | El oráculo: crear set → sembrar → curar a mano → **congelar** |
| `stratum.py` | Carga de un disparo del estrato positivo como cohorte adicional |
| `canonical_refs.py` | Re-mapea los `job_ref` tras la canonización legacy — la **segunda mitad** de la maniobra. **Ejecutado el 2026-08-27**: 6 298 filas canonizadas, 10 juicios y 162 pares re-mapeados |
| `inbox.py` | El transporte de la sombra: inserción síncrona e idempotente en `jobhunt.shadow_inbox` |
| `RUNBOOK.md` | La operación: diagnóstico del slot, reinicio, rollback, emergencia, cadencias, canonización |

### 2.8 Tareas y cadencias del core

18 tareas `jobhunt.*` `[I]` y **9 cadencias** en el beat embebido `[V]`:

| Cadencia | Tarea | Cuándo |
|---|---|---|
| 5 min | `jobhunt.shadow.sample_outbox_lag` | muestreo del retardo del outbox |
| 5 min | `jobhunt.shadow.check_slot_health` | salud del slot |
| 5 min | `jobhunt.shadow.project` | proyección del staging |
| 5 min | `jobhunt.delivery.dispatch_outbox` | despacho de entregas |
| **1 h** | `jobhunt.idempotency.purge_expired` | purga de idempotencia — **no** cada 5 min |
| diaria 05:20 | `jobhunt.maintenance.dedup_scan` | antes del barrido |
| diaria 05:35 | `jobhunt.maintenance.archive_sweep` | **antes** del cierre de ciclo |
| diaria 06:05 | `jobhunt.shadow.run_cycle` | cierre de ciclo y gate |
| diaria 06:40 | `jobhunt.maintenance.purge_retention` | **después** del cierre de ciclo |

El orden diario no es casual: cada cita está colocada respecto a las 06:05 por una razón
distinta. El barrido va antes para que el gate mida el corpus ya podado; la purga va
después porque el ciclo cuenta evaluaciones y dead-letters de la ventana que acaba de
cerrar, y purgar antes le cambiaría los números bajo los pies. **Moverlas cambia lo que el
gate mide.**

---

## 3. ReactPortfolio (`/home/lothar/Public/ReactPortfolio/`)

Portfolio web con UI de ventanas flotantes; también BFF del core, y el **piloto** de la
Fase C.

### 3.1 Backend

Routers `[I]`: analytics (3), auth, chat, **20 routers de fuente** (uno por portal),
`jobs_unified` (búsqueda unificada, **consumidor de la costura de catálogo**), `ai_match`
(**costura de matching**), `job_applications` (**costura de candidaturas**),
`saved_searches` (**costura de búsquedas**), notifications (SSE), cv_export,
cv_generation, cv_profiles, schools, y dos routers de administración del cutover:
`cutover.py` (`POST /push-cv`) y `gate_c.py` (`POST /readiness`).

Servicios destacados `[I]`: `job_service` (utilidades compartidas y constantes),
`job_matcher` (matching en dos etapas), `analysis_runner` (el análisis del dashboard corre
en segundo plano: *start* / *progress* / *result*, persistido en Redis 24 h),
`daily_match_report` (digest diario **incremental**: sólo analiza lo ausente del libro
`seen_jobs`, y sólo marca como visto cuando el envío se confirma),
`cv_generation_service` (Gemini primario → Groq de respaldo), `chat_service`,
`saved_search_runner`, `write_freeze`, `token_blacklist`, `refresh_replay`, `gate_c`.

**Cuatro capacidades de costura** `[I]`: `catalog`, `matching`, `applications`,
`saved_searches` — dos menos que SwissJob, porque el portfolio no tiene documentos ni
colegios sobre el core.

**El transporte** es `services/core_http.py`: un `httpx.AsyncClient` con `base_url` a la red
interna de compose (nunca por ngrok), `Authorization: Bearer`, y **timeouts asimétricos** —
conexión corta (5 s, para detectar rápido un core caído) y lectura larga (120 s, porque la
primera llamada al feed tras un arranque es fría). Encima de los timeouts hay
**presupuestos agregados**, porque el timeout de lectura de httpx es por lectura de socket,
no por petición.

**Diferencia de diseño con SwissJob que conviene tener presente:** el portfolio es
mono-propietario, así que **no tiene** `jobhunt_profile_map` — el UUID del perfil del core
sale de la configuración, no de la base. Y sus cuatro capacidades se enrutan por la **fila
comodín**, no por perfil.

**Estado verificado en la instancia local** `[V]`: dos filas, ambas comodín — `catalog` y
`matching` en `core_read`, del 2026-08-25. `applications` y `saved_searches` sin fila ⇒
`local`. La producción del NAS se describe con las cuatro capacidades sobre el core `[I]`;
no he podido verificarla.

### 3.2 Frontend

React 19 + Vite 7. La metáfora es un escritorio: cada sección es una ventana flotante
arrastrable y redimensionable (`FloatingWindow`), con acordeón en móvil y accesibilidad
(`role="dialog"`, Escape).

El detalle de diseño que merece mención en un inventario: `WindowContext` está **partido en
dos contextos** `[I]` — estado y callbacks — para que un consumidor que sólo use callbacks
no re-renderice cuando cambie el estado.

Los datos de empleo se consumen en hooks: `useJobFilter` (el consumidor real de la costura:
guarda el token de secuencia y decide entre cursor del core y offset local),
`useDashboardData`, `useAIJobMatch`, `useSavedSearches`, `useKanban`. El registro único de
las 20 fuentes vive en `config/jobSources.js`: añadir una fuente se hace ahí y sólo ahí.

### 3.3 Tests

94 ficheros en el backend `[I]`, agrupados por router/servicio, por costura (~15) y por
**ciclo de auditoría G3–G8** (~22, cada hallazgo con su guarda ejecutable). 32 ficheros de
test unitario en el frontend más 4 *specs* de Playwright.

> **Punto ciego declarado, y es importante:** la suite del portfolio corre en **SQLite** y
> producción en **PostgreSQL** `[I]`. SQLite no impone longitudes de `varchar`, ni el rango
> de entero de 32 bits, ni el repertorio de caracteres. Ningún test de integración ve los
> fallos de esa clase; las guardas de esos límites tienen que ser **unitarias**.

---

## 4. Piezas transversales

| Pieza | Dónde | Qué garantiza |
|---|---|---|
| **Redacción de secretos** | `backend/utils/redact.py` | Vive en **un solo sitio** y se aplica en las **dos raíces** (filtro de logging sobre los handlers del root, y el registro de diagnósticos de fetch), no parcheando cada llamada. `jobhunt_core/` no tiene redacción: su autenticación va por cabecera, no por query string `[I]` |
| **Coacción de repertorio** | `ReactPortfolio/backend/utils/text_repertoire.py` | Elimina U+0000 y *surrogates* sueltos —que `json.loads` acepta y el driver no puede codificar— en valores **y claves**. Idempotente: una cadena sana vuelve tal cual |
| **Un solo proceso** | `ReactPortfolio/backend/scripts/docker-entrypoint.sh` | `--workers 1` **explícito** `[V]`. De él cuelgan cuatro subsistemas, incluido el limitador de ritmo (`MemoryStorage` = estado de proceso) y con él la puerta anti-fuerza-bruta del login |
| **Cumplimiento** | `backend/services/compliance.py` + tabla `source_compliance` | Sin fila para una fuente ⇒ `can_scrape=False` ⇒ bloqueo silencioso. Sembrar la fila es parte de dar de alta un scraper |
| **Circuit breaker** | `backend/services/circuit_breaker.py` | Uno por fuente, no uno global: una fuente caída no arrastra a las demás |

---

## 5. Dónde vive el conocimiento que no está en ningún `.md`

Tres modelos normativos existen **sólo como docstring**, y el código no hace nada que no
esté ahí `[I]`:

1. **La máquina de estados de la familia de refresh** — `ReactPortfolio/backend/routers/auth.py`.
   Estados, roles, el **orden de las guardas** (que es parte del modelo) y la ventana de
   gracia.
2. **La coacción de repertorio** — `ReactPortfolio/backend/utils/text_repertoire.py`.
3. **La garantía de un solo proceso** — el entrypoint más la guarda que **cuenta procesos**
   (no nombres de variable) en `tests/test_idempotency_retry_audit.py`.

En el core, el equivalente son los docstrings de `delivery.py` y `shadow/gate.py`, que
llevan el registro de por qué cada guarda es como es, ciclo a ciclo. Son largos a propósito:
tres de esos mecanismos se rompieron al «simplificarlos».

---

## Continuar

- **[DOC 1 — Blueprint](DOC_1_BLUEPRINT_2026-08-27.md)** — el problema, las decisiones y el
  estado del proyecto.
- **[DOC 3 — Detalle técnico](DOC_3_DETALLE_TECNICO_2026-08-27.md)** — cómo funcionan por
  dentro las piezas importantes, con sus cotas y sus vías muertas.
