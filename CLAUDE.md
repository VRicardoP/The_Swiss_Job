# CLAUDE.md — SwissJobHunter

> Proyecto: Agregador de búsqueda de empleo con IA para Suiza.
> Multi-portal, multi-idioma (DE/FR/EN/IT). Webapp standalone multi-usuario.
> Ver `PORTALES_EMPLEO_SUIZA.md` para especificaciones completas (2200+ líneas).

---

## Contexto rápido

- **Backend**: FastAPI + Celery + PostgreSQL (pgvector) + Redis
- **Frontend**: React + TailwindCSS v4 + Vite
- **Workers**: Celery (providers queue cada 6h, scraping queue cada 6h)
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

- **NO scraping** de: jobs.ch, jobup.ch, Indeed, LinkedIn, Glassdoor, XING
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
providers/          # 16 providers (BaseJobProvider + CircuitBreaker)
scrapers/           # 7 scrapers (BaseScraper extends BaseJobProvider)
services/
  job_matcher.py    # pipeline 3 etapas: pgvector → multi-factor → Groq LLM
  translation.py    # títulos a inglés via Groq (DE/FR/IT only)
  groq_service.py   # sync SDK + run_in_threadpool + Redis cache 7d
  compliance.py     # ComplianceEngine + kill-switch (3 bloques → disable)
tasks/
  fetch_jobs.py     # queue: default, schedule: cada 6h
  scraping_tasks.py # queue: scraping, schedule: cada 6h
api/                # FastAPI routers
models/             # SQLAlchemy + Pydantic schemas
```

> Para detalles de cada componente, consultar `.claude/memory/MEMORY.md`
