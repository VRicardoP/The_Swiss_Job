# CLAUDE.md — SwissJobHunter

> Proyecto: Agregador de búsqueda de empleo con IA para Suiza.
> Multi-portal, multi-idioma (DE/FR/EN/IT). Webapp standalone multi-usuario.
> Ver `PORTALES_EMPLEO_SUIZA.md` para especificaciones completas (2200+ líneas).

---

## Contexto rápido

- **Backend**: FastAPI + Celery + PostgreSQL (pgvector) + Redis
- **Frontend**: React + TailwindCSS v4 + Vite
- **Workers**: Celery, despachado por APScheduler (`services/scheduler.py`) — cosecha diaria autónoma (fetch→scrape→embed→dedup→match a hora base 12:00 CET ± 4 h de jitter, `SCHEDULER_DAILY_HARVEST_ENABLED=True` por defecto) o, en modo intervalos, providers cada **30 min** (`SCHEDULER_FETCH_INTERVAL_MINUTES`) y scrapers cada **6 h** (`SCHEDULER_SCRAPER_INTERVAL_HOURS`). Además, SIEMPRE: dedup semántico 04:00, chequeo de URLs **diario** 03:00 (no semanal — el log de resumen de `scheduler.py` todavía dice «weekly Sun», es una cadena obsoleta), limpieza de caducadas 03:30, búsquedas guardadas cada 60 min, salud de watchlist cada 6 h, digest de watchlist 18:00, alerta profesor cada 6 h y digest diario de matches (opt-in, `DAILY_DIGEST_ENABLED=False` por defecto)
- **Core (Fase A)**: paquete `jobhunt_core/` — API v1 FastAPI (`core-api`, puerto 8003), `core-worker` Celery (tareas `jobhunt.*`, broker `redis-core`, colas `core.*`, **beat embebido** `-B`, 10 cadencias: cada **5 min** sampler de lag del outbox, salud del slot, proyector sombra y despacho del outbox; cada **hora** la purga de idempotencia (`CORE_IDEMPOTENCY_PURGE_EVERY_S=3600` — *no* cada 5 min) y la **salud de la cosecha** (`jobhunt.harvest.check_health`, G9 P2-C: alerta si un scope acumula fallos o lleva días sin cosecha completa); y cuatro citas diarias en este orden: dedup-scan 05:20 → archive-sweep 05:35 → ciclo sombra 06:05 → purga de retención 06:40; al arrancar registra el transporte sombra → `jobhunt.shadow_inbox`), migraciones propias vía `core-migrate` (cadena `core0001..core0033`). **Desde `ae7fbf2` la imagen del core es INMUTABLE**: el compose base ya no monta `./jobhunt_core` en `core-api`/`core-worker`/`core-capture`/`core-migrate`, y para trabajar sobre el árbol de trabajo hay que pedir el override `-f docker-compose.yml -f docker-compose.dev.yml` (ver «Perfiles de compose» abajo)
- **Sombra (Fase B, SOLO LOCAL)**: CDC legacy→core por slot lógico `jobhunt_shadow` (postgres custom `docker/postgres-core/` con wal2json, `wal_level=logical`) → servicio `core-capture` (staging con ack tras commit) → proyector → métricas y GATE-SOMBRA (7 ciclos). Módulos `jobhunt_core/shadow/`; operación: `jobhunt_core/shadow/RUNBOOK.md`
- **Documentación de referencia**: **cotas aceptadas y decisiones deliberadas → `docs/COTAS_Y_DECISIONES.md`** (léelo ANTES de "arreglar" cualquier limitación: varias se intentaron cerrar y el intento fue peor que la cota); estado y contadores vigentes → `ESTADO_Y_HOJA_DE_RUTA.md` **§20** (§19 es la foto anterior a la jornada del 2026-08-27); core → `PLAN_UNIFICACION_JOBHUNTING.md` (§23–§24) y `CONTRATOS_FASE_A.md`, los tres en `/home/lothar/Public/`; legacy → `docs/`

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

# Tests backend (2.280 passed · 3 skipped · 4 xfailed)
# OJO: NO lances dos pytest a la vez — el teardown hace TRUNCATE ... CASCADE de
# swissjobhunter_test y las dos corridas se vacían las tablas entre sí (deadlocks + falsos rojos)
docker compose exec -T backend python -m pytest tests/ -v --timeout=30

# Tests core (Fase A/B/C, 689 passed — reconfirmar con pytest tras cada crecida)
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

# Migraciones core (cadena core0001..core0033 — las aplica core-migrate en el arranque).
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
providers/          # 25 providers (20 activos + 5 restringidos gated); BaseJobProvider + CircuitBreaker
  restricted.py     # jobs.ch/LinkedIn/Indeed/Glassdoor/XING SOLO por ruta autorizada (partner/feed); OFF por defecto
scrapers/           # 15 scrapers (7 base incl. irishjobs + 8 swiss_schools_*); BaseScraper extends BaseJobProvider
services/
  job_matcher.py    # pipeline 3 etapas: pgvector → multi-factor → LLM (Groq rerank, fallback Gemini).
                    #   SEIS pesos: embedding .35 · salary .15 · location .10 · recency .15 · llm .15 · language .10
                    #   Etapa 1 SIN LIMIT a propósito (decide el umbral, no un top-K) y ya no transporta el
                    #   embedding: la distancia viene como columna (defer+raiseload). MATCH_SCORE_THRESHOLD=42.0 (.env)
  translation_service.py  # títulos a inglés via GROQ_RERANK_MODEL=qwen3.6-27b (DE/FR/IT only)
  groq_service.py   # sync SDK + run_in_threadpool; rerank cae a Gemini si Groq falla (también si Groq responde
                    #   basura, no solo si lanza). Caché de rerank POR OFERTA (esquema v3): la clave es la
                    #   proyección de la oferta + huella del perfil + modelo + prompt de sistema — NUNCA el
                    #   índice ni el orden del lote, que era lo que la hacía fallar siempre (0 claves vivas)
  gemini_service.py # Google Gemini 2.5 Flash — PRIMARIO de generación de CV/carta (httpx); fallback Groq gpt-oss-120b
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
                    #   delivery/runs/profiles, Alembic propio core0001..core0033, tests 689/689 (vía core-migrate)
```

Modelos LLM: `GROQ_MODEL=openai/gpt-oss-120b` (fallback docs), `GROQ_RERANK_MODEL=qwen/qwen3.6-27b`
(traducción + rerank, requiere `reasoning_effort=none` — lo envía GroqService automáticamente), Gemini `gemini-2.5-flash`
(primario docs). Decomisos Groq: `llama-3.3-70b-versatile` (2026-08-16), `llama-4-scout` (2026-07-17).

> Para detalles de cada componente, consultar `docs/` (legacy) y, para el core,
> `PLAN_UNIFICACION_JOBHUNTING.md` y `CONTRATOS_FASE_A.md` en `/home/lothar/Public/`
