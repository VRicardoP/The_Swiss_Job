# Guía de traslado — Pipeline autónomo + Crawler incremental + Fuentes restringidas

> **Propósito:** replicar EXACTAMENTE en un proyecto paralelo (misma función:
> agregador de empleo con IA) todo lo implementado en esta sesión.
> **Fecha:** 2026-07-04. **Stack destino esperado:** FastAPI + Celery + APScheduler +
> PostgreSQL(pgvector) + Redis + SQLAlchemy 2.0 (Mapped/mapped_column) + Alembic.
> **Eje rector:** *el volumen de peticiones depende del número de ofertas NUEVAS, no del total.*

Este documento es autocontenido: describe cada componente, su responsabilidad, las
firmas exactas y los fragmentos de código no triviales. Al final hay un **checklist de
replicación** paso a paso.

---

## 0. Resumen de lo implementado

Tres bloques, en orden de dependencia:

1. **Pipeline autónomo diario** — extracción + matching + alertas sin intervención del
   usuario, una vez al día a hora VARIABLE (patrón circadiano).
2. **Crawler incremental (cursores + early-stop)** — el scraping deja de paginar en
   cuanto reconoce contenido ya visto. Volumen ∝ ofertas nuevas.
3. **Fuentes restringidas (`restricted_ready`)** — jobs.ch/jobup.ch, LinkedIn, Indeed,
   Glassdoor, XING cableadas como conectores deshabilitados por defecto, activables solo
   por vía autorizada (partner API / feed / import). NO scraping público.

Documento de diseño conceptual que sustenta todo: **`a.txt`** (raíz del proyecto) —
crawler incremental, integración de fuentes restringidas y §10 catálogo + EVALUACIÓN de
técnicas anti-bot para uso unipersonal local (sin proxies).

---

## 1. Pipeline autónomo diario

### 1.1 Orquestador `daily_harvest` (nuevo: `tasks/pipeline_tasks.py`)

Cadena Celery de firmas **inmutables** (`.si()`, orden garantizado): cada eslabón
arranca cuando el anterior TERMINA.

```python
from celery import chain

@celery_app.task(name="tasks.pipeline.daily_harvest", bind=True, max_retries=0)
def daily_harvest(self) -> dict:
    from tasks.embedding_tasks import embed_all_pending
    from tasks.fetch_tasks import fetch_providers
    from tasks.maintenance_tasks import dedup_semantic_batch
    from tasks.matching_tasks import run_all_matches
    from tasks.scraping_tasks import fetch_scrapers

    workflow = chain(
        fetch_providers.si(),
        fetch_scrapers.si(),
        embed_all_pending.si(batch_size=200),
        dedup_semantic_batch.si(batch_size=500),
        run_all_matches.si(),
    )
    result = workflow.apply_async()
    return {"status": "dispatched", "chain_id": result.id}
```

La alerta de colegios (email) NO va en la cadena: corre en su propio schedule cada N
horas (solo depende de la ingesta + su marca de agua).

### 1.2 Matching automático `run_all_matches` (nuevo: `tasks/matching_tasks.py`)

El matching solo se disparaba vía API. Nueva tarea para todos los perfiles con embedding:

```python
async def _run_all_matches_async() -> dict:
    from database import task_session
    from models.user_profile import UserProfile
    from services.gemini_service import GeminiService
    from services.groq_service import GroqService
    from services.match_service import MatchService

    groq, gemini = GroqService(), GeminiService()
    summary = {"profiles": 0, "results": 0, "skipped": 0, "errors": 0}
    async with task_session() as db:
        profiles = (await db.execute(
            select(UserProfile).where(UserProfile.cv_embedding.is_not(None))
        )).scalars().all()
        service = MatchService(db, groq=groq, gemini=gemini)
        for p in profiles:
            result = await service.run_matching(p.user_id)
            summary["profiles"] += 1
            summary["results" if result.get("status") == "success" else "skipped"] += (
                result.get("results_count", 0) if result.get("status") == "success" else 1
            )
    return summary
```

**Coste IA acotado por diseño:** etapas 1 (pgvector) + 2 (multi-factor) son LOCALES; solo
el top-N (`MATCH_LLM_RERANK_TOP=50`) va al LLM. Nada se pierde por ese límite: TODO lo que
supera `MATCH_SCORE_THRESHOLD` se guarda; el 50 solo limita la afinación LLM.

### 1.3 `embed_all_pending` (en `tasks/embedding_tasks.py`)

Drena TODOS los embeddings pendientes en bucle (modelo local, sin coste API) antes del
matching. Se extrajo un helper `_embed_pending_batch(db, matcher, batch_size)` reutilizado
por la tarea de un solo lote (`generate_job_embeddings`) y por `embed_all_pending`:

```python
async def _embed_all_pending_async(batch_size: int) -> dict:
    matcher = JobMatcher()
    total = 0
    async with task_session() as db:
        while True:
            n = await _embed_pending_batch(db, matcher, batch_size)
            total += n
            if n < batch_size:
                break
    if total:
        dedup_semantic_batch.delay(batch_size=min(max(total, 200), 1000))
    return {"status": "success", "processed": total}
```

### 1.4 Scheduler — hora variable (patrón circadiano) en `services/scheduler.py`

Con `SCHEDULER_DAILY_HARVEST_ENABLED` (default True) se registra `daily_harvest` con
`CronTrigger(hour, jitter)` — cae a hora distinta cada día. El fetch por intervalos pasa
a modo legacy (rama `else`).

```python
if settings.SCHEDULER_DAILY_HARVEST_ENABLED:
    jitter_seconds = settings.SCHEDULER_DAILY_HARVEST_JITTER_HOURS * 3600
    scheduler.add_job(
        _dispatch_daily_harvest,
        CronTrigger(hour=settings.SCHEDULER_DAILY_HARVEST_HOUR, minute=0,
                    timezone="Europe/Zurich", jitter=jitter_seconds),
        id="daily_harvest", replace_existing=True,
    )
else:
    # fetch clásico por intervalos (fetch_providers / fetch_scrapers)
    ...

def _dispatch_daily_harvest() -> None:
    celery_app.send_task("tasks.pipeline.daily_harvest")
```

### 1.5 Gemini como fallback del re-ranking (resiliencia)

`GroqService.rerank_jobs(..., fallback=gemini)` + método `_rerank_call` (Groq primario →
Gemini si Groq falla/caduca/no configurado). Protege contra caducidad de `GROQ_API_KEY`
(HTTP 401) que degradaba el rerank en silencio.

```python
async def _rerank_call(self, user_prompt: str, fallback) -> str:
    if self.is_available:
        try:
            return await self.get_chat_response(
                user_message=user_prompt, system_prompt=RERANK_SYSTEM_PROMPT,
                model=settings.GROQ_RERANK_MODEL,
                temperature=settings.GROQ_RERANK_TEMPERATURE,
                max_tokens=settings.GROQ_RERANK_MAX_TOKENS)
        except Exception:
            logger.warning("Groq rerank falló; intentando fallback (Gemini)")
    if fallback is not None and getattr(fallback, "is_available", False):
        return await fallback.get_chat_response(
            user_message=user_prompt, system_prompt=RERANK_SYSTEM_PROMPT,
            temperature=settings.GROQ_RERANK_TEMPERATURE,
            max_tokens=settings.GROQ_RERANK_MAX_TOKENS)
    raise RuntimeError("Sin proveedor LLM disponible para el re-ranking")
```

`MatchService.__init__(db, groq=None, gemini=None)` guarda `self.gemini`, expone
`_llm_available` (Groq O Gemini) y `_stage3_llm_rerank` pasa `fallback=self.gemini`. El
router `/analyze` construye `MatchService(db, groq=groq, gemini=GeminiService())`.

### 1.6 Gating del self-chain (fetch_tasks / scraping_tasks)

Con la cosecha diaria activa, el auto-encadenado de embeddings NO se dispara (la cadena ya
lo cubre) — evita doble trabajo/carreras:

```python
if result.get("new", 0) > 0 and not settings.SCHEDULER_DAILY_HARVEST_ENABLED:
    generate_job_embeddings.delay(batch_size=100)
```

### 1.7 Registro en `celery_app.py`

Añadir a `conf.include`: `"tasks.matching_tasks"`, `"tasks.pipeline_tasks"`.

---

## 2. Crawler incremental (cursores + early-stop)

**Idea:** guardar por fuente una ventana de identidades (URLs) de ofertas recientes ya
vistas; inyectarla en el scraper antes de paginar; parar en cuanto una página completa ya
es conocida. Así el nº de páginas ∝ ofertas nuevas.

### 2.1 Modelo `SourceCursor` (nuevo: `models/source_cursor.py`)

Tabla `source_cursors`. Campos clave: `source_key`, `scope_key` (default `"default"`),
`cursor_type` (`url`|`hash`|`timestamp`, hoy `url`), `recent_identities` (JSONB, ventana de
URLs), `bootstrap_complete`, métricas (`avg_new_jobs_per_run`, `avg_pages_per_run`,
`consecutive_empty_runs`, `consecutive_errors`), timestamps (`last_run_at`,
`last_success_at`, `last_empty_at`, `high_watermark_seen_at`, `created_at`, `updated_at`).
`UniqueConstraint(source_key, scope_key)`. Registrar en `models/__init__.py` (import + `__all__`).

### 2.2 Migración Alembic

Dos migraciones encadenadas desde el head previo:
- `add_source_cursors` — `op.create_table("source_cursors", ...)` + índice en `source_key`.
- `seed_restricted_compliance` — ver §3.

Aplicar: `docker compose run --rm backend alembic upgrade head`.

### 2.3 Identidad + early-stop en `BaseJobProvider` (`services/job_service.py`)

```python
def __init__(self):
    ...
    self._known_urls: set[str] = set()   # inyectado por el pipeline antes de fetch
    self._stop_reason: str | None = None

@staticmethod
def job_identity(job: dict) -> str:
    return (job.get("url") or job.get("detail_url")
            or job.get("source_id") or job.get("hash") or "").strip()

def _page_all_known(self, page_jobs: list[dict]) -> bool:
    """True si TODA la página ya se vio (ninguna novedad) según _known_urls.
    Con _known_urls vacío nunca corta (comportamiento legacy)."""
    if not self._known_urls or not page_jobs:
        return False
    return all(self.job_identity(j) in self._known_urls for j in page_jobs)
```

### 2.4 Early-stop en los bucles de paginación (`services/scraper_engine.py`, `BaseScraper`)

En **ambos** bucles (`_scrape_with_httpx` y `_scrape_with_playwright`), tras
`all_jobs.extend(...)` y ANTES del corte por `len(stubs) < PAGE_SIZE`:

```python
if self._page_all_known(stubs):
    self._stop_reason = "known_page"
    logger.info("%s early-stop en página %d: sin ofertas nuevas (cursor)",
                self.SOURCE_NAME, page)
    break
```

### 2.5 `CursorStore` (nuevo: `services/cursor_store.py`)

```python
class CursorStore:
    async def load(self, db, source_key, scope_key="default") -> SourceCursor:
        # SELECT por (source_key, scope_key); crea vacío con db.add + await db.flush() si no existe
    @staticmethod
    def known_identities(cursor) -> set[str]:
        return set(cursor.recent_identities or [])
    def update_after_run(self, cursor, fetched_identities, new_count, pages_read) -> None:
        # prepende fetched a recent_identities (dedup, cap CURSOR_RECENT_IDENTITIES_MAX),
        # EMA de métricas (alpha=0.3), timestamps, consecutive_empty_runs, bootstrap_complete=True
        # NO commitea: lo hace el pipeline
```

### 2.6 Integración en el pipeline (`tasks/scraping_tasks.py::_fetch_scrapers_async`)

Gated por `settings.CURSOR_INCREMENTAL_ENABLED`. Por cada scraper (bucle secuencial con db):

```python
store = CursorStore() if settings.CURSOR_INCREMENTAL_ENABLED else None
...
if store is not None:
    cursor = await store.load(db, source)
    scraper._known_urls = store.known_identities(cursor)

jobs = await scraper.fetch_jobs("", "Switzerland")   # early-stops internamente
fetched_identities = [scraper.job_identity(j) for j in jobs]
new_before = summary["new"]
# ... bucle per-job savepoint (upsert) igual que antes ...
if store is not None and cursor is not None:
    pages_read = max(1, math.ceil(len(jobs) / max(scraper.PAGE_SIZE, 1)))
    store.update_after_run(cursor, fetched_identities,
                           new_count=summary["new"] - new_before, pages_read=pages_read)
await db.commit()
```

> **Nota de alcance (honesta):** el early-stop de peticiones se aplica a la capa que
> pagina fuerte = **scrapers** (bucle compartido en `BaseScraper`). Los **providers de
> API** ya hacen peticiones acotadas (≤3 páginas, O(1) respecto al total), por lo que ya
> cumplen el eje; su coste aguas abajo se reduce porque embeddings/matching solo procesan
> lo nuevo. Si un scraper concreto sobrescribe el bucle (p.ej. scroll AJAX), hay que
> aplicarle el mismo `_page_all_known` en su bucle propio.

---

## 3. Fuentes restringidas (`restricted_ready`)

### 3.1 Conectores (nuevo: `providers/restricted.py`)

Base `RestrictedPartnerProvider(BaseJobProvider)` + 5 subclases. Sin credencial →
`auth_missing` (0 peticiones). NUNCA scraping público.

```python
class RestrictedPartnerProvider(BaseJobProvider):
    CREDENTIAL_ATTR = ""      # nombre del setting con la credencial
    AUTHORIZED_ROUTE = ""     # doc de activación
    def _credential(self) -> str:
        return getattr(settings, self.CREDENTIAL_ATTR, "") or ""
    async def fetch_jobs(self, query, location="Switzerland"):
        if not self._credential():
            logger.info("%s deshabilitado: auth_missing (%s vacío). Ruta: %s",
                        self.SOURCE_NAME, self.CREDENTIAL_ATTR, self.AUTHORIZED_ROUTE)
            return self._finalize_fetch([])
        logger.warning("%s: credencial presente pero conector partner sin implementar",
                       self.SOURCE_NAME)
        return self._finalize_fetch([])
    def normalize_job(self, raw): ...  # esqueleto al esquema unificado
```

Subclases (SOURCE_NAME / CREDENTIAL_ATTR):
- `jobcloud_partner` / `JOBCLOUD_PARTNER_API_KEY` (jobs.ch / jobup.ch)
- `linkedin_authorized` / `LINKEDIN_PARTNER_TOKEN`
- `indeed_partner` / `INDEED_PARTNER_KEY`
- `glassdoor_partner` / `GLASSDOOR_PARTNER_KEY`
- `xing_partner` / `XING_PARTNER_TOKEN`

### 3.2 Registro con gating (`providers/__init__.py`)

Añadir las 5 clases a `_PROVIDER_CLASSES` **y** a `_KEY_REQUIREMENTS` (mapeadas a su
setting de credencial). Así `get_all_providers()` (que filtra por `_has_required_key`) NO
las instancia sin credencial: arrancan deshabilitadas, pero aparecen en
`get_provider_names()` / `log_provider_status()` como "preparadas".

### 3.3 Seed de compliance (migración Alembic)

`INSERT INTO source_compliance (...) VALUES (...) ON CONFLICT (source_key) DO NOTHING;`
con `is_allowed=false`, `method='api'`, `tos_notes='RESTRICTED (...): solo vía ... NO
scraping público.'` para las 5 fuentes.

---

## 4. Config añadido (`config.py`)

```python
# Cosecha diaria autónoma
SCHEDULER_DAILY_HARVEST_ENABLED: bool = True
SCHEDULER_DAILY_HARVEST_HOUR: int = 12          # hora base CET
SCHEDULER_DAILY_HARVEST_JITTER_HOURS: int = 4   # ± → distinta hora cada día

# Crawler incremental
CURSOR_INCREMENTAL_ENABLED: bool = True
CURSOR_RECENT_IDENTITIES_MAX: int = 300

# Fuentes restringidas (vacío = conector deshabilitado)
JOBCLOUD_PARTNER_API_KEY: str = ""
LINKEDIN_PARTNER_TOKEN: str = ""
INDEED_PARTNER_KEY: str = ""
GLASSDOOR_PARTNER_KEY: str = ""
XING_PARTNER_TOKEN: str = ""
```

`GEMINI_API_KEY` (ya existía para documentos) pasa a ser también el fallback del matching.
Reflejar en `.env.example` y `.env.prod.example` (secciones Scheduler + Gemini + restringidas).

---

## 5. Tests añadidos

- `tests/test_scheduler.py` — modo cosecha diaria vs legacy + jitter del trigger.
- `tests/test_matching_automation.py` — `_rerank_call` Groq→Gemini, `rerank_jobs` sin
  proveedor → [], `run_all_matches` agrega perfiles (mock DB/servicios).
- `tests/test_incremental_crawler.py` — `job_identity`, `_page_all_known` (early-stop),
  `CursorStore.update_after_run`/`known_identities`, conectores restringidos
  (`auth_missing`, gating en el registro, forma de `normalize_job`).
- `tests/test_provider_registry.py` — actualizado a 25 providers (20 + 5 restringidas).

`pytest.ini` usa `asyncio_mode = auto` (tests async sin decorador). Los tests necesitan
Postgres+Redis (fixture auto-create de la BD de test en `conftest.py`).

---

## 6. Ficheros tocados (inventario)

**Nuevos:**
`tasks/pipeline_tasks.py`, `tasks/matching_tasks.py`, `services/cursor_store.py`,
`models/source_cursor.py`, `providers/restricted.py`,
`alembic/versions/d4e6f8a0b2c1_add_source_cursors.py`,
`alembic/versions/e6f8a0b2c4d3_seed_restricted_compliance.py`,
`tests/test_matching_automation.py`, `tests/test_incremental_crawler.py`.

**Modificados:**
`config.py`, `celery_app.py`, `services/scheduler.py`, `services/match_service.py`,
`services/groq_service.py`, `routers/match.py`, `tasks/embedding_tasks.py`,
`tasks/fetch_tasks.py`, `tasks/scraping_tasks.py`, `services/job_service.py`,
`services/scraper_engine.py`, `models/__init__.py`, `providers/__init__.py`,
`tests/test_scheduler.py`, `tests/test_provider_registry.py`,
`.env.example`, `.env.prod.example`. Doc de diseño: `a.txt`.

---

## 7. Checklist de replicación exacta (proyecto paralelo)

1. [ ] Copiar/portar `a.txt` (documento de diseño rector).
2. [ ] `config.py`: añadir los settings de §4.
3. [ ] Modelo `SourceCursor` + registrar en `models/__init__.py`.
4. [ ] Migración `add_source_cursors` (down_revision = head actual del proyecto paralelo).
5. [ ] `services/cursor_store.py` (`CursorStore`).
6. [ ] `BaseJobProvider`: `_known_urls`/`_stop_reason` en `__init__`, `job_identity`,
       `_page_all_known` (§2.3).
7. [ ] `BaseScraper`: early-stop en los DOS bucles de paginación (§2.4).
8. [ ] Pipeline de scraping: cargar cursor, inyectar `_known_urls`, actualizar cursor (§2.6).
9. [ ] `providers/restricted.py` (base + 5) + registrar en `_PROVIDER_CLASSES` y
       `_KEY_REQUIREMENTS` (§3.1-3.2).
10. [ ] Migración seed de compliance restringida (§3.3).
11. [ ] `embed_all_pending` + helper `_embed_pending_batch` (§1.3).
12. [ ] `tasks/matching_tasks.py::run_all_matches` (§1.2).
13. [ ] Gemini fallback del rerank: `groq_service.rerank_jobs(fallback)` + `_rerank_call`
        + `MatchService(gemini)` + wiring del router (§1.5).
14. [ ] `tasks/pipeline_tasks.py::daily_harvest` (cadena) (§1.1).
15. [ ] Scheduler: `daily_harvest` con jitter + rama legacy (§1.4).
16. [ ] Gating del self-chain en fetch/scraping tasks (§1.6).
17. [ ] `celery_app.py conf.include`: añadir `matching_tasks`, `pipeline_tasks`.
18. [ ] `.env.example` / `.env.prod.example`: reflejar settings nuevos.
19. [ ] Tests de §5 (adaptar counts del registro de providers).
20. [ ] `alembic upgrade head` + `pytest` en el entorno del proyecto paralelo.

---

## 8. Decisiones y notas de comportamiento

- **No se filtra por IA ANTES de guardar en `jobs`.** La tabla `jobs` guarda todo (dedup,
  recencia, alerta de colegios que necesita ofertas de categoría docente que el perfil del
  usuario penalizaría). El "filtro IA" es el matching (condicionado al perfil), automático
  y con coste acotado (top-N al LLM). Lo relevante-para-el-usuario se materializa en
  `match_results`.
- **Límite top-50 del LLM ≠ pérdida.** Todo lo que supera el umbral se guarda; el 50 solo
  limita la afinación LLM. Gemini de fallback añade resiliencia, no “amplía” el 50.
- **Early-stop conservador:** se corta cuando una página ENTERA ya es conocida
  (`_page_all_known`). Tolera reordenamientos suaves; si un portal intercala patrocinadas
  de forma agresiva, subir la ventana (`CURSOR_RECENT_IDENTITIES_MAX`) o migrar a umbral de
  racha.
- **No-bypass en producción.** Las fuentes restringidas se activan solo por vía autorizada.
  Las técnicas anti-bot de `a.txt §10` son para EVALUACIÓN unipersonal local (sin proxies),
  no para el scraping automático programado.
