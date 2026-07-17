# Scraping que simula un humano — SwissJobHunter

> **Estado:** implementado y en producción (última pieza: `CrawlerBudgetService`, 2026-07-18).
> **Origen:** apuntes del curso de web scraping (`web_scraping_course_notes.docx`) +
> plan de rediseño `docs/PLAN_STEALTH_SCRAPER_JOBUP.md`.
> **Principio rector:** el sistema no gana por volumen ni por evasión, sino por
> **arquitectura**: parecer un navegador real, pedir poco y solo lo nuevo, y parar
> ante cualquier señal de bloqueo.

Este documento reúne, en un solo sitio, todas las piezas que hacen que la cosecha
de ofertas se comporte como una persona navegando de forma esporádica y no como un
crawler exhaustivo. Son cuatro capas complementarias.

---

## Resumen en una frase

> Un navegador Chrome creíble (capa 1), que consulta a horas irregulares del día
> (capa 2), pide solo las páginas necesarias para las ofertas nuevas y salta las
> fuentes sin novedades (capa 3), y se detiene —convirtiéndose en estado de
> compliance— en cuanto detecta un bloqueo (capa 4).

`requests_por_run ≈ 1 + ceil(ofertas_nuevas / page_size)` — **nunca** `total_ofertas / page_size`.

---

## Capa 1 — Parecer un navegador real (huella de cliente)

**Fichero:** `backend/services/scraper_stealth.py` (utilidades puras, sin efectos) +
`backend/services/scraper_engine.py` (las aplica).

| Técnica | Qué hace | Dónde |
|---------|----------|-------|
| **Cabeceras realistas** | User-Agent de Chrome 131 en Linux + client hints (`Sec-CH-UA`) + `Sec-Fetch-*` coherentes con una navegación de documento. No fija `Accept-Encoding` (deja negociar a httpx). | `realistic_headers()` |
| **Delays con jitter** | Retardo aleatorio entre peticiones en `[base, base·(1+ratio)]`. Los intervalos perfectamente regulares delatan a un bot. | `jittered_delay()`, `SCRAPER_DELAY_JITTER_RATIO=0.5` |
| **Playwright endurecido** | `init_script` que enmascara `navigator.webdriver`, expone `plugins`/`languages`/`window.chrome` y neutraliza la API de permisos. Args `--disable-blink-features=AutomationControlled`, viewport 1920×1080, locale `de-CH`. | `STEALTH_INIT_SCRIPT`, `STEALTH_LAUNCH_ARGS` |
| **Reintentos con backoff** | Errores transitorios (timeouts, 5xx) se reintentan con backoff exponencial + jitter, no en ráfaga. | `SCRAPER_MAX_RETRIES=2`, `SCRAPER_RETRY_BACKOFF_SECONDS=2.0` |

**Idioma:** `Accept-Language` prioriza alemán suizo (`de-CH,de;q=0.9,fr;q=0.8,en;q=0.7`),
coherente con la mayoría de portales del proyecto.

**Opcional (off por defecto), para portales muy protegidos:**
- `SCRAPER_PROXY_URL` — enruta httpx + Playwright por un proxy/rotación externa.
- `SCRAPER_BROWSER_CDP_URL` — conecta Playwright a un browser stealth remoto por CDP.

---

## Capa 2 — Consultar a horas de humano (patrón circadiano)

**Fichero:** `backend/services/scheduler.py` + `backend/tasks/pipeline_tasks.py`.

En vez de cosechar en intervalos fijos de reloj (señal clara de automatización), la
**cosecha diaria autónoma** (`daily_harvest`) se ejecuta **una vez al día a una hora
variable** dentro de la franja diurna:

- Hora base `SCHEDULER_DAILY_HARVEST_HOUR=12` (CET), con **jitter de ±`SCHEDULER_DAILY_HARVEST_JITTER_HOURS=4` horas**.
- Cada día cae a una hora distinta → no hay un patrón horario reconocible.
- La cadena encadena todo el pipeline sin intervención: `fetch → scrape → embed → dedup → match`.

Con `SCHEDULER_DAILY_HARVEST_ENABLED=True` (default), el fetch por intervalos
(providers/scrapers cada 6h) pasa a modo legacy y no se registra.

---

## Capa 3 — Pedir poco y solo lo nuevo (incremental + presupuesto)

Es el corazón del enfoque "humano": una persona no repagina un portal entero cada
día, solo mira lo que ha aparecido desde la última vez. Dos mecanismos.

### 3.1 Crawler incremental (early-stop por cursor)

**Ficheros:** `models/source_cursor.py`, `services/cursor_store.py`,
`services/job_service.py` (`BaseJobProvider`), `services/scraper_engine.py`.

- Cada fuente/scope guarda en `source_cursors` una **ventana de URLs recientes ya vistas**
  (`recent_identities`, tope `CURSOR_RECENT_IDENTITIES_MAX=300`).
- Antes de cada run, el pipeline inyecta esas identidades en `scraper._known_urls`.
- Cuando una página entera ya es conocida (`_page_all_known()`), el scraper **deja de
  paginar** (`_stop_reason="known_page"`). Con el cursor vacío nunca corta (comportamiento legacy).
- Cubre los tres caminos de paginación: httpx, Playwright y el **scroll AJAX de schuljobs**
  (early-stop en la página inicial y en cada fragmento).
- Tras el run, `update_after_run()` refresca la ventana y las métricas EMA
  (`avg_new_jobs_per_run`, `avg_pages_per_run`, `consecutive_empty_runs`).

**Resultado:** el volumen de peticiones es proporcional a las ofertas **nuevas**, no al
tamaño del portal. Un portal reordena o mezcla patrocinadas → la ventana corta evita
cortar demasiado pronto.

### 3.2 Presupuesto explícito de peticiones (`CrawlerBudgetService`) — NUEVO

**Fichero:** `backend/services/crawler_budget.py`. Decisiones **puras** (sin I/O) a partir
del historial que el cursor ya acumulaba. Convierte "recolectar poco" de efecto colateral
del early-stop en **política explícita** (sección 7 del plan).

| Decisión | Fórmula | Efecto |
|----------|---------|--------|
| **Páginas por run** (`max_pages_this_run`) | `ceil(max(avg_new, 1) / page_size) + CRAWLER_BUDGET_SAFETY_PAGES`, acotado a `[1, MAX_PAGES]` | Portal con 50.000 ofertas pero ~12 nuevas/día → **1-2 páginas**. Bootstrap pendiente → ventana de arranque completa. |
| **Backoff de frecuencia** (`should_run`) | Con `consecutive_empty_runs ≥ CRAWLER_BUDGET_EMPTY_RUNS_THRESHOLD` (3), el intervalo exigido se **duplica** por cada run vacío extra, con tope `CRAWLER_BUDGET_BACKOFF_MAX_MULTIPLIER` (×4) | Una fuente parada se salta runs enteros → **0 peticiones** ese run. |

**Integración** (`tasks/scraping_tasks.py`):
- `budget_on = cursores activos + CRAWLER_BUDGET_ENABLED`.
- Intervalo base: 24h con la cosecha diaria, o `SCHEDULER_SCRAPER_INTERVAL_HOURS` en modo intervalos.
- Salta la fuente (`summary["skipped"]`) o fija `scraper._max_pages_this_run`.
- `BaseJobProvider._pages_budget()` = `min(MAX_PAGES, presupuesto)` (mínimo 1), respetado en httpx, Playwright y en scrapers que extienden `BaseJobProvider` directamente (Workday ISP).
- **Sin migraciones**: reutiliza columnas ya presentes en `SourceCursor`.

Comportamiento esperado por situación:

| Situación | Peticiones/run |
|-----------|----------------|
| Fuente sin cambios (dentro del backoff) | **0** |
| Fuente con 1-20 novedades (`page_size=20`) | 1 (+margen) |
| Fuente con 21-40 novedades | 2 (+margen) |
| Portal enorme pero pocas novedades | 1-2 |
| Bootstrap inicial | hasta `MAX_PAGES` |

---

## Capa 4 — No evadir: parar ante el bloqueo

**Fichero:** `services/scraper_stealth.py` (`looks_soft_blocked`) + `services/compliance.py`.

El diseño **no** intenta sortear WAF, CAPTCHA, login ni paywalls. Un bloqueo es una señal
de parada, no un problema a rodear:

- **Soft-block** (HTTP 200 sin datos + marcador anti-bot de alta confianza:
  `/cdn-cgi/challenge-platform`, `verify you are human`, etc.) → se reporta a compliance.
  "captcha" a secas se excluye a propósito (falsos positivos con widgets legítimos).
- **403 / 429 / 503** → `_report_block()` y se deja de paginar.
- **Kill-switch de compliance:** 3 bloqueos consecutivos → la fuente se auto-deshabilita.
- Caso real: **med-jobs.com deshabilitado** por un challenge duro de Cloudflare que el
  Playwright local no supera. No se insiste; se reactivaría solo con un browser stealth remoto de pago.

---

## Mapa de configuración (todos los settings en `backend/config.py`)

```python
# Capa 1 — huella de navegador
SCRAPER_DELAY_JITTER_RATIO          = 0.5      # jitter en delays (hasta +50%)
SCRAPER_MAX_RETRIES                 = 2
SCRAPER_RETRY_BACKOFF_SECONDS       = 2.0
SCRAPER_PROXY_URL                   = ""       # opt-in (proxy externo)
SCRAPER_BROWSER_CDP_URL             = ""       # opt-in (browser stealth remoto)

# Capa 2 — patrón circadiano
SCHEDULER_DAILY_HARVEST_ENABLED     = True
SCHEDULER_DAILY_HARVEST_HOUR        = 12       # hora base CET
SCHEDULER_DAILY_HARVEST_JITTER_HOURS = 4       # ±4h → hora distinta cada día

# Capa 3.1 — crawler incremental
CURSOR_INCREMENTAL_ENABLED          = True
CURSOR_RECENT_IDENTITIES_MAX        = 300

# Capa 3.2 — presupuesto explícito (NUEVO)
CRAWLER_BUDGET_ENABLED              = True
CRAWLER_BUDGET_SAFETY_PAGES         = 1
CRAWLER_BUDGET_EMPTY_RUNS_THRESHOLD = 3
CRAWLER_BUDGET_BACKOFF_MAX_MULTIPLIER = 4
```

---

## Tests

| Área | Fichero |
|------|---------|
| Huella de navegador (headers, jitter, soft-block) | `tests/test_scraper_stealth.py` |
| Early-stop incremental + cursores + fuentes restringidas | `tests/test_incremental_crawler.py` |
| Presupuesto (`max_pages_this_run`, `should_run`) | `tests/test_crawler_budget.py` |
| Presupuesto en el engine (`_pages_budget`, tope httpx) | `tests/test_scraper_engine.py::TestBaseScraperPageBudget` |

Suite completa: **647 tests** (2026-07-18), ruff limpio.

---

## Lo que queda pendiente (ver `PLAN_STEALTH_SCRAPER_JOBUP.md` §14)

Estas piezas del plan **no** están implementadas — el enfoque actual las cubre parcialmente
o las suple con alternativas más simples:

- **Scheduler por scopes vencidos** (`next_run_at` por fuente). Hoy: schedule fijo +
  backoff por fuente dentro del run (`should_run`).
- **Peticiones condicionales `ETag` / `Last-Modified`** y **sitemap `lastmod`**.
- **Conectores ATS estructurados** (Greenhouse, Lever, Ashby, SmartRecruiters) — hoy solo
  hay un caso Workday ad-hoc en `swiss_schools_isp`.
- **Cursores en providers de API** (hoy el cursor solo se inyecta en scrapers).
- **UI/admin** para mostrar fuentes restringidas como "preparadas, pendientes de autorización".

> **No es objetivo** simular movimientos de ratón/scroll humanos ni ninguna otra evasión
> conductual: el plan lo descarta explícitamente. Se gana por arquitectura, no por engaño.
