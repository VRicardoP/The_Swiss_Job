# CLAUDE.md — SwissJobHunter

> Proyecto: Agregador de búsqueda de empleo con IA para Suiza.
> Multi-portal, multi-idioma (DE/FR/EN/IT). Webapp standalone multi-usuario.
> Ver `PORTALES_EMPLEO_SUIZA.md` para especificaciones completas (2200+ líneas).

---

## Contexto rápido

- **Backend**: FastAPI + Celery + PostgreSQL (pgvector) + Redis
- **Frontend**: React + TailwindCSS v4 + Vite
- **Workers**: Celery — cosecha diaria autónoma (fetch→scrape→embed→dedup→match a hora variable con jitter, `SCHEDULER_DAILY_HARVEST_ENABLED`) o, en modo intervalos, providers/scraping cada 6h; + alerta profesor cada 6h
- **Memoria del proyecto**: `.claude/memory/` — leer `MEMORY.md` al inicio de sesión

---

## Puertos Docker (mapeados al host para evitar conflictos)

| Servicio   | Puerto host |
|------------|-------------|
| PostgreSQL | **5435**    |
| Redis      | **6380**    |
| Backend    | **8002**    |
| Frontend   | **5174**    |

> Servicios en conflicto en el host: 5433, 5434 (postgres), 6379 (redis), 5678 (n8n), 8001

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

# Tests backend
docker compose exec -T backend python -m pytest tests/ -v --timeout=30

# Linting
docker compose exec -T backend ruff check --no-cache .
docker compose exec -T backend ruff format --check --no-cache .

# Migraciones
docker compose exec backend alembic upgrade head
docker compose exec backend alembic revision --autogenerate -m "descripcion"

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
scrapers/           # 14 scrapers (6 base + 8 swiss_schools_*); BaseScraper extends BaseJobProvider
services/
  job_matcher.py    # pipeline 3 etapas: pgvector → multi-factor → LLM (Groq rerank, fallback Gemini)
  translation_service.py  # títulos a inglés via GROQ_RERANK_MODEL=qwen3.6-27b (DE/FR/IT only)
  groq_service.py   # sync SDK + run_in_threadpool + Redis cache 7d; rerank cae a Gemini si Groq falla
  gemini_service.py # Google Gemini 2.5 Flash — PRIMARIO de generación de CV/carta (httpx); fallback Groq gpt-oss-120b
  email_service.py  # SMTP stdlib para avisos (SMTP_* en config)
  teacher_alert.py  # detecta docencia primaria (categoría H job_classifier + nivel) → email
  cursor_store.py   # crawler INCREMENTAL: cursor de URLs recientes por fuente/scope (early-stop)
  crawler_budget.py # presupuesto explícito: páginas por run según novedades medias + backoff de fuentes sin cambios
  scraper_stealth.py # capa anti-detección (headers Chrome, jitter, soft-block, Playwright endurecido)
  compliance.py     # ComplianceEngine + kill-switch (3 bloques → disable)
  # Scraping "humano" (4 capas: huella navegador + circadiano + incremental/presupuesto + no-evasión): docs/SCRAPING_HUMANO.md
tasks/
  pipeline_tasks.py # COSECHA DIARIA autónoma: fetch→scrape→embed→dedup→match, hora variable (jitter)
  fetch_tasks.py / scraping_tasks.py  # fetch API cada 6h / scrapers cada 6h (modo intervalos)
  matching_tasks.py # matching automático de todos los perfiles con embedding
  alert_tasks.py    # alerta profesor primaria por email (cada 6h)
api/                # FastAPI routers
models/             # SQLAlchemy + Pydantic (incl. source_cursor.py para el crawler incremental)
```

Modelos LLM: `GROQ_MODEL=openai/gpt-oss-120b` (fallback docs), `GROQ_RERANK_MODEL=qwen/qwen3.6-27b`
(traducción + rerank, requiere `reasoning_effort=none` — lo envía GroqService automáticamente), Gemini `gemini-2.5-flash`
(primario docs). Decomisos Groq: `llama-3.3-70b-versatile` (2026-08-16), `llama-4-scout` (2026-07-17).

> Para detalles de cada componente, consultar `.claude/memory/MEMORY.md`
