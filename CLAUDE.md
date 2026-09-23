# CLAUDE.md — SwissJobHunter

> Proyecto: Agregador de búsqueda de empleo con IA para Suiza.
> Multi-portal, multi-idioma (DE/FR/EN/IT). Webapp standalone multi-usuario.
> Ver `PORTALES_EMPLEO_SUIZA.md` para especificaciones completas (2200+ líneas).

---

## Contexto rápido

- **Backend**: FastAPI + Celery + PostgreSQL (pgvector) + Redis
- **Frontend**: React + TailwindCSS v4 + Vite
- **Cosecha: NATIVA desde el 2026-09-22.** Las 16 fuentes del corpus las cosecha
  el core (`jobhunt.harvest.dispatch_native`, 4 ventanas diarias 00:10/06:10/12:10/18:10
  Europe/Zurich, una tarea acotada por scope). Los productores legacy están
  retirados por `LEGACY_DISABLED_PROVIDERS` / `LEGACY_DISABLED_SCRAPERS`: listas de
  ARRANQUE validadas contra el registro, un nombre desconocido impide construir el
  productor. Acta: `docs/audits/POINT4_CUTOVER_2026-09-22.md`
- **Workers legacy que SIGUEN y por qué**: `swissjob-worker` conserva 5 providers
  (`zebis` y `publicjobs` a propósito — alimentan la alerta de profesor de primaria,
  que lee `jobs.category='H'` de la base pública; `jobicy`, `proz` y `remoteco` nunca
  se transfirieron) y **13 scrapers**, de los cuales los 8 `swiss_schools_*` son el
  colector escolar que publica observaciones al core. APScheduler sigue despachando
  dedup semántico 04:00, chequeo de URLs 03:00, limpieza 03:30, salud de watchlist
  cada 6 h, digest de watchlist 18:00, alerta profesor cada 6 h y digest diario
  (opt-in). La cadena `daily_harvest` sigue existiendo pero su etapa de fetch ya no
  construye las fuentes transferidas
- **Core (Fase A)**: paquete `jobhunt_core/` — API v1 FastAPI (`core-api`, puerto 8003), `core-worker` Celery (tareas `jobhunt.*`, broker `redis-core`, colas `core.*`, **beat embebido** `-B`, 14 cadencias: cada **5 min** sampler de lag del outbox, salud del slot, proyector sombra y despacho del outbox; cada **hora** la purga de idempotencia (`CORE_IDEMPOTENCY_PURGE_EVERY_S=3600` — *no* cada 5 min) y la **salud de la cosecha** (`jobhunt.harvest.check_health`, G9 P2-C: alerta si un scope acumula fallos o lleva días sin cosecha completa; G10 P3-4 + G11 P3-2: publica SIEMPRE el censo del parque y avisa cuando no observa a todos los habilitados — `alertas: []` sobre un parque medio invisible se leía como cosecha sana); y cuatro citas diarias en este orden: dedup-scan 05:20 → archive-sweep 05:35 → ciclo sombra 06:05 → purga de retención 06:40; al arrancar registra el transporte sombra → `jobhunt.shadow_inbox`), migraciones propias vía `core-migrate` (cadena `core0001..core0051`). **Desde `ae7fbf2` la imagen del core es INMUTABLE**: el compose base ya no monta `./jobhunt_core` en `core-api`/`core-worker`/`core-capture`/`core-migrate`, y para trabajar sobre el árbol de trabajo hay que pedir el override `-f docker-compose.yml -f docker-compose.dev.yml` (ver «Perfiles de compose» abajo)
- **Sombra (Fase B, SOLO LOCAL)**: CDC legacy→core por slot lógico `jobhunt_shadow` (postgres custom `docker/postgres-core/` con wal2json, `wal_level=logical`) → servicio `core-capture` (staging con ack tras commit) → proyector → métricas y GATE-SOMBRA (7 ciclos). Módulos `jobhunt_core/shadow/`; operación: `jobhunt_core/shadow/RUNBOOK.md`
- **Documentación de referencia**: **cotas aceptadas y decisiones deliberadas → `docs/COTAS_Y_DECISIONES.md`** (léelo ANTES de "arreglar" cualquier limitación: varias se intentaron cerrar y el intento fue peor que la cota); estado y contadores vigentes → `ESTADO_Y_HOJA_DE_RUTA.md` **§46** (la foto del 2026-09-23; §45 y anteriores son fotos previas y NO deben leerse como vigentes); core → `PLAN_UNIFICACION_JOBHUNTING.md` (§23–§24) y `CONTRATOS_FASE_A.md`, los tres en `/home/lothar/Public/`; legacy → `docs/`

---

## Puertos Docker (mapeados al host para evitar conflictos)

| Servicio   | Puerto host |
|------------|-------------|
| PostgreSQL | **5435**    |
| Redis      | **6380**    |
| Redis core | **6381** (solo loopback) |
| Backend    | **8002**    |
| Core API   | **8003**    |
| Frontend   | **5174**    |

> Servicios en conflicto en el host: 5433, 5434 (postgres), 6379 (redis), 5678 (n8n), 8001

---

## Perfiles de compose — operativo vs. desarrollo (auditoría externa 2026-08-27, P1-3)

`docker-compose.yml` es el perfil **OPERATIVO**: los cuatro servicios del core corren
el código **de la imagen**, sin bind mount. `RELEASE_SHA` se hornea como build arg
(ARG → ENV en el Dockerfile) y **no** aparece en ningún `environment:`, para que no
pueda desligarse del código que identifica.

`docker-compose.dev.yml` es el perfil de **DESARROLLO** y hay que **pedirlo**:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml <cmd>
```

Devuelve los cuatro bind mounts y pone `CORE_CODE_MUTABLE=1`, con lo que `/v1/ready`
responde `authoritative: false`. **No** es un `docker-compose.override.yml` a propósito:
un override implícito se aplicaría también al desplegar y devolvería el defecto en
silencio; olvidarse del `-f` deja el perfil **seguro**, no el mutable.

**Qué cambia para quien trabaja aquí:** todo comando del core que deba ver el árbol de
trabajo —los tests, sobre todo— necesita los dos `-f`. Sin ellos se probaría el código
de la IMAGEN.

Identidad de la release, comprobable en vez de confiada:

```bash
curl -s localhost:8003/v1/health   # {"release": "<sha>", "alembic_expected": "…", "authoritative": true}
curl -s localhost:8003/v1/ready    # {"release": "<sha>", "authoritative": true, ...}
```

`authoritative: true` exige código inmutable (sin el override de desarrollo) **y** un
`release` nombrable: con `RELEASE_SHA=unknown` la comparación «todos publican el mismo
SHA» se cumpliría entre `unknown`s sin significar nada (auditoría G9 P2-A/P2-B).

`core-api` tiene además healthcheck de compose contra `/v1/ready` (sin él, la API
estuvo dos días en 503 sin que nadie se enterara). `docker-compose.prod.yml` y
`.qnap.yml` **NO** lo tienen todavía: son de producción y no se tocaron.

---

## Restricciones del proyecto

- **NO scraping PÚBLICO** de: jobs.ch, jobup.ch, Indeed, LinkedIn, Glassdoor, XING. `providers/restricted.py` permite integrarlos SOLO por ruta autorizada (credencial partner / feed oficial); arrancan deshabilitados (sin credencial → 0 peticiones, nunca scraping)
- Nunca modificar `.env` ni `docker-compose.yml` sin confirmación explícita
- Tests siempre contra la DB `swissjobhunter_test` — nunca contra producción
- Tareas Celery con `def`, no `async def`. Patrón: `def task(): asyncio.run(_impl())`
- Comentarios en español para lógica no obvia; código y nombres en inglés

---

## Rendimiento del feed servido — invariante que cuesta caro romper

Desde el punto 5 (2026-09-22) el BFF **no recorre el feed entero** para servir
una página: el core informa del `total` en su primera página y el consumidor
deja de paginar al cubrir `offset + limit`. Antes servir 20 ofertas costaba 18
peticiones internas y 9-13 s; ahora 1 petición y 0,56-1,19 s en el método.

**El punto 5 sigue ABIERTO.** Medido el 2026-09-23 sobre `point5-9d6b46e`
desplegado en los cinco servicios: la pantalla principal pasó de **79,3 s a
p50 2,147 s** (p95 2,554 s) y la primera carga de ~54 s a 10,196 s. Aun así
**ninguna lectura habitual baja del p95 de 2 s** (catálogo 2,420 s; feed 20
3,040 s) y la primera carga de 3.000 dobla su presupuesto de 5 s. Matriz
completa con veredicto por escenario en §9-bis del acta; lo que falta, en §10.
Los invariantes de abajo valen igual.

**El recorrido que de verdad pide la pantalla principal es `limit=3000`**
(`MatchPage.jsx:40` → `useMatchResults(3000, 0)`, `translate=false`), no una
página de 20. Es la LECTURA HABITUAL, no una exportación: el lote completo
alimenta las categorías y sus contadores, la pestaña Watchlist, el top score,
los matches ≥ 70 **y** las tarjetas visibles. Bajar el límite sin mover esos
agregados al servidor rompe las cinco cosas. (`useMatchResultsPage`, la única
consulta con `translate=true`, es código muerto.)

Tres cosas que NO deben deshacerse sin medir:

1. `MatchesPageDTO.total` es **aditivo y opcional**. Si falta, el consumidor
   vuelve al recorrido completo a propósito: sin ese dato el recorrido ES lo que
   produce el número, y cortar antes daría un total falso.
2. El recuento usa `effective_feedback_batch_sql`, **no** la forma correlacionada
   (1,1-9,3 s frente a 0,4-0,9 s para el mismo número), y se apoya en el índice
   parcial `ix_pvs_feed_current_eval` (`core0051`). El `current_eval_id IS NOT NULL`
   redundante del WHERE es lo que deja al planificador alcanzarlo.
3. La rama de `CORE_FEEDBACK_ENABLED = false` **conserva el recorrido completo**:
   allí sí hay exclusiones locales y el total es un subconjunto recalculado.

4. **El idioma de la oferta VIAJA; no se deduce al servir.** `VacancyDTO.language`
   (aditivo y opcional) → `_vacancy_dtos` → `_job_view` → router. Antes el campo
   se perdía en las tres capas y `_to_match_response` detectaba el idioma de
   CADA oferta servida: 50,1 ms x 1.800 = ~90 s por petición, el cuello real de
   la pantalla principal (79 s extremo a extremo). Un valor no-cadena se sirve
   como AUSENTE, nunca como error: es un indicador, no la identidad.
   Fijado por `backend/tests/test_language_transport.py` y
   `jobhunt_core/tests/test_vacancy_language.py`.
   **Pero sólo el 3,1 % del feed trae el dato.** Para el 96,9 % restante el
   idioma se DERIVA una vez por título y se PERSISTE en `job_title_languages`
   (migración `d3a7c1f60b84`): lo resuelve `tasks.language_tasks` cada 5 min en
   lotes acotados, y servir sólo lee. Los tres estados son explícitos — fila
   ausente = nunca visto, `language IS NULL` = encolado, `language = ''` =
   resuelto como DESCONOCIDO (y por eso no se reintenta).

5. **El camino de respuesta NO detecta idioma.** Ni con el dato ausente. Si
   falta, la tarjeta va sin indicador y la tarea de fondo lo resuelve para la
   carga siguiente. `tests/test_language_store.py` instala un detector que
   LANZA: devolver la detección al router hace explotar la prueba en vez de
   ponerla lenta otra vez. La memoización de `_detect_language` se queda, pero
   ya es de segundo orden — una caché en proceso no cubre la primera carga.

6. **El recorrido completo del feed se cachea por VERSIÓN, no por tiempo.**
   `GET /v1/profiles/{id}/matches/version` da un digest sobre
   `vacancy_id:current_eval_id:current_offer_revision_id:primary_incarnation_id:updated_at`
   de todo el feed; el BFF reutiliza su recorrido sólo si coincide EXACTAMENTE
   **y** lo relee al terminar el recorrido (un recorrido de 18 páginas no es
   atómico). Los dos últimos componentes se añadieron el 23-09 tras una
   regresión VIVA: el `state.feedback` viaja en el payload cacheado y, con
   `CORE_FEEDBACK_ENABLED=True`, un `thumbs_up` se servía como `null` tras el
   ACK. Además `CoreFeedback._write` invalida la caché del perfil tras cada
   escritura. **`feed()` NO excluye vacantes sin canónica a propósito**: es el
   feed que mide el nDCG del gate; recuento y versión sí las excluyen. Motivo medido:
   recorrer el feed es el **87-89 %** del coste de la pantalla principal
   (47,5 s frío / 11,5 s caliente, 18 páginas). Caliente seguía costando
   porque **un `If-None-Match` no ahorra cómputo**: el ETag se deriva del
   payload, así que el core construye la página igual para contestar 304.
   Cuatro cotas que NO deben deshacerse: sólo se cachea el recorrido
   **completo**; sólo se pregunta la versión si `needed > 100`; el overlay
   LOCAL (candidatura, urgencia, borrador) se relee siempre y el estado del
   core está cubierto por la versión; y sin versión fiable **se recorre**. Fijado por
   `backend/tests/test_feed_version_cache.py` y
   `jobhunt_core/tests/test_matches_version.py`.

Acta y mediciones: `docs/audits/ACTA_CIERRE_PUNTO5_2026-09-22.md`.
La cola de latencia restante **no está atribuida**. Ni el loadavg del NAS ni el
mínimo observado la explican: un mínimo no separa trabajo de espera.

---

## Principios de diseño (máxima prioridad)

1. **Single Responsibility** — cada módulo/clase/función hace UNA cosa
2. **Cohesion** — la lógica relacionada permanece junta
3. **Low Coupling** — depender de abstracciones, no implementaciones
4. **Readability** — claridad sobre ingeniosidad; los nombres revelan intención

Estos principios tienen prioridad sobre velocidad, brevedad o DRY.

---

## Comandos clave

```bash
# Arrancar entorno completo
docker compose up -d

# Tests backend (2.562 passed · 4 xfailed)
# OJO: NO lances dos pytest a la vez — el teardown hace TRUNCATE ... CASCADE de
# swissjobhunter_test y las dos corridas se vacían las tablas entre sí (deadlocks + falsos rojos)
docker compose exec -T backend python -m pytest tests/ -v --timeout=30

# Tests core (1.765 passed, ~16 min — reconfirmar con pytest tras cada crecida)
# OJO al perfil: desde la auditoría P1-3 el compose BASE no monta ./jobhunt_core
# (imagen operativa inmutable). Los tests van con el override de desarrollo, que
# es el que monta el árbol de trabajo; sin él se probaría el código de la IMAGEN.
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
  run --rm core-migrate python -m pytest jobhunt_core/tests

# Linting
docker compose exec -T backend ruff check --no-cache .
docker compose exec -T backend ruff format --check --no-cache .

# Migraciones legacy (la BD local está en `b3c7d1a95e42`, aplicada el 2026-08-27:
# pone clock_timestamp() en los defaults de jobs.first_seen_at/last_seen_at y
# match_results.created_at — verificado en la base real, no solo en el fichero)
docker compose exec backend alembic upgrade head
docker compose exec backend alembic revision --autogenerate -m "descripcion"

# Migraciones core (cadena core0001..core0051 — las aplica core-migrate en el arranque).
# Sin override: aplica las migraciones DE LA IMAGEN, que es lo correcto al operar.
# Para probar una migración nueva del árbol de trabajo, añade los dos -f del perfil dev.
docker compose run --rm core-migrate python -m jobhunt_core.migrate

# Logs en tiempo real
docker compose logs -f backend
docker compose logs -f worker
```

## Skills disponibles en este proyecto

| Skill | Alias | Cuándo usarla |
|-------|-------|---------------|
| `/audit` | `AUDIT` | Auditoría técnica profunda — deuda técnica, bugs, vulnerabilidades, cobertura de tests, rendimiento |
| `/audit-prod` | `AUDIT_PROD` | Auditoría de producción estricta — release blockers, seguridad crítica, fiabilidad, observabilidad |
| `/docsync` | `DOCSYNC` | Sincronizar documentación, memoria, skills y hooks — elimina obsoletos, optimiza lo que crece |

Los skills leen los prompts canónicos en `.ai/prompts/` y añaden contexto del proyecto (memoria, arquitectura) para análisis paralelo con subagentes especializados.

---

## Arquitectura en una página

```
providers/          # 25 REGISTRADOS, pero desde el traspaso solo 5 se construyen
                    # (LEGACY_DISABLED_PROVIDERS). El registro conserva los nombres a
                    # propósito: el catálogo y el histórico siguen resolviéndolos
  restricted.py     # jobs.ch/LinkedIn/Indeed/Glassdoor/XING SOLO por ruta autorizada (partner/feed); OFF por defecto
scrapers/           # 15 scrapers (7 base incl. irishjobs + 8 swiss_schools_*); BaseScraper extends BaseJobProvider
services/
  job_matcher.py    # pipeline 3 etapas: pgvector → multi-factor → LLM (Groq rerank, fallback Gemini).
                    #   SEIS pesos: embedding .35 · salary .15 · location .10 · recency .15 · llm .15 · language .10
                    #   Etapa 1 SIN LIMIT a propósito (decide el umbral, no un top-K) y ya no transporta el
                    #   embedding: la distancia viene como columna (defer+raiseload). MATCH_SCORE_THRESHOLD=42.0 (.env)
  translation_service.py  # títulos a inglés via GROQ_RERANK_MODEL=qwen3.8-27b (DE/FR/IT only)
  groq_service.py   # sync SDK + run_in_threadpool; rerank cae a Gemini si Groq falla (también si Groq responde
                    #   basura, no solo si lanza). Caché de rerank POR OFERTA (esquema v3): la clave es la
                    #   proyección de la oferta + huella del perfil + modelo + prompt de sistema — NUNCA el
                    #   índice ni el orden del lote, que era lo que la hacía fallar siempre (0 claves vivas)
  gemini_service.py # Google Gemini 3.6 Flash — PRIMARIO de generación de CV/carta (httpx); fallback Groq gpt-oss-120b
  email_service.py  # SMTP stdlib para avisos (SMTP_* en config)
  teacher_alert.py  # detecta docencia primaria (categoría H job_classifier + nivel) → email
  cursor_store.py   # crawler INCREMENTAL: cursor de URLs recientes por fuente/scope (early-stop)
  crawler_budget.py # presupuesto explícito: páginas por run según novedades medias + backoff de fuentes sin cambios.
                    #   La EMA se AUTOLIMITABA (su insumo iba capado por el propio techo) y perdía ofertas para
                    #   siempre, en silencio. Hoy: run que agota presupuesto SIN early-stop = «con hambre» ⇒ la
                    #   pasada siguiente reabre el bootstrap. Ver docs/COTAS_Y_DECISIONES.md §4
  scraper_stealth.py # capa anti-detección (headers Chrome, jitter, soft-block, Playwright endurecido)
  compliance.py     # ComplianceEngine + kill-switch (3 bloques → disable)
  # Scraping "humano" (4 capas: huella navegador + circadiano + incremental/presupuesto + no-evasión): docs/SCRAPING_HUMANO.md
tasks/
  pipeline_tasks.py # COSECHA DIARIA autónoma: fetch→scrape→embed→dedup→match, hora variable (jitter)
  fetch_tasks.py / scraping_tasks.py  # modo intervalos: providers cada 30 min, scrapers cada 6h.
                    #   Los 15 scrapers corren EN SERIE a propósito (paralelizar = otra huella; ver COTAS §4)
  matching_tasks.py # matching automático de todos los perfiles con embedding
  alert_tasks.py    # alerta profesor primaria por email (cada 6h)
routers/            # FastAPI routers (NO existe backend/api/)
schemas/            # Pydantic de entrada/salida de la API
models/             # SQLAlchemy (incl. source_cursor.py para el crawler incremental)
jobhunt_core/       # Core Fase A COMPLETA 2026-07-24 (ensayo GATE A superado): API /v1 FastAPI (core-api :8003),
                    #   worker Celery jobhunt.* (broker redis-core, colas core.*), harvest/ + matching/embeddings/
                    #   delivery/runs/profiles, Alembic propio core0001..core0051, tests 1.765/1.765 (vía core-migrate)
```

Modelos LLM (verificados contra el catálogo VIVO el 2026-09-15):
`GROQ_MODEL=openai/gpt-oss-120b` (fallback docs), `GROQ_RERANK_MODEL=qwen/qwen3.8-27b`
(traducción + rerank), Gemini `gemini-3.6-flash` (primario docs).
**Groq decomisa modelos sin avisar y el fallo es MUDO**: `qwen3.6-27b` devolvía
`model_not_found` y la traducción de títulos y el Stage 3 llevaban tiempo caídos sin
que nada lo gritara. Comprobar `GET /openai/v1/models` antes de dar por bueno un
modelo. Decomisos: `llama-3.3-70b-versatile` (2026-08-16), `llama-4-scout`
(2026-07-17), `qwen3.6-27b` (2026-09-15). `reasoning_effort=none` ya no es necesario
con 3.8 pero se mantiene como protección ante un futuro modelo que razone.

> Para detalles de cada componente, consultar `docs/` (legacy) y, para el core,
> `PLAN_UNIFICACION_JOBHUNTING.md` y `CONTRATOS_FASE_A.md` en `/home/lothar/Public/`
