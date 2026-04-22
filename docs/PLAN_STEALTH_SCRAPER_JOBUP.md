# Plan: Stealth Scraper — jobup.ch

> **Status:** Planificado — pendiente de implementación
> **Fecha:** 2026-03-12
> **Riesgo legal:** Uso personal únicamente. Ver sección "Consideraciones legales".

---

## 1. Contexto

jobup.ch (propiedad de JobCloud AG) es el portal de empleo #1 de Suiza francófona
(~2.9M visitas/mes, ~48K ofertas activas). Sus TOS prohíben scraping automatizado,
por lo que está en la lista de portales prohibidos del proyecto.

Este documento planifica un scraper "stealth" que simula comportamiento humano
navegando el portal a ritmo natural. **Solo para uso personal de búsqueda de empleo.**

---

## 2. Reconocimiento técnico de jobup.ch

### 2.1 Stack tecnológico
- **React SPA** con datos pre-cargados en objeto `__INIT__` (similar a `__NEXT_DATA__`)
- **Job Search API:** `https://job-search-api.jobup.ch` (API interna consumida por el frontend)
- **Analytics:** Tealium UTG + Microsoft Clarity
- **Error tracking:** Sentry
- **CDN/WAF:** AWS (CloudFront + WAF)

### 2.2 Anti-bot
- **AWS WAF CAPTCHA** (`AWS_CAPTCHA_REGIONAL` token en config)
- **Bot detection activa:** campo `botDetect.clientClassification` en datos de página
- **Cookies HTTP-only** para sesiones autenticadas

### 2.3 Estructura de URLs
```
Listado:   https://www.jobup.ch/en/jobs/
Filtrado:  https://www.jobup.ch/en/jobs/?term=python&location=zurich
Paginado:  https://www.jobup.ch/en/jobs/?page=2
Detalle:   https://www.jobup.ch/en/jobs/detail/{job-id}/
```

### 2.4 Datos en listado vs detalle
| Listado (card)                              | Detalle (página)              |
|---------------------------------------------|-------------------------------|
| title, company, logo, location              | Descripción completa          |
| employment grade (%), employment type        | Requisitos detallados         |
| publication date, salary (si disponible)     | Info de contacto/aplicación   |
| `isPaid`, `isNew`, `isActive` flags         | Skills, benefits, etc.        |

### 2.5 Paginación
- 20 resultados por página (configurable: `DEFAULT_SEARCH_RESULTS: 20`)
- Total: ~2424 páginas (`meta.numPages`)
- Parámetro: `?page=N`
- Datos embebidos en `__INIT__` JSON dentro de `<script>` tag

### 2.6 robots.txt
- `/en/jobs/` **NO está bloqueado** — las páginas de búsqueda son crawleables
- No hay directiva `Crawl-delay`
- Sitemap disponible: `https://www.jobup.ch/sitemaps/sitemap.xml`
- Solo SemrushBot está bloqueado completamente (`Disallow: /`)

---

## 3. Estrategia: Simulación de navegación humana

### 3.1 Principio fundamental
El scraper NO hace requests masivas. Simula un usuario real que:
1. Abre el portal
2. Navega por las ofertas (20 por página)
3. Hace scroll, lee, a veces hace clic en un detalle
4. Cierra la sesión después de un rato

### 3.2 Perfil de sesión
```
Sesión = {
    páginas_por_sesión: 15-25        (aleatorio)
    delay_entre_páginas: 20-45s      (aleatorio, distribución normal μ=30s)
    delay_entre_detalles: 10-25s     (simulando lectura)
    prob_click_detalle: 0.3          (30% de ofertas → visita detalle)
    duración_sesión: ~15-25 min
    sesiones_por_día: 4-6            (espaciadas 2-4 horas)
    pausa_entre_sesiones: 2-4h       (aleatorio)
}
```

### 3.3 Fases de operación

#### Fase A: Crawl inicial (días 1-2)
- **Objetivo:** Recopilar el catálogo existente (~48K ofertas)
- **Ritmo:** ~20 ofertas/página × 20 páginas/sesión × 5 sesiones/día = **~2000 ofertas/día**
- **Solo datos de listado** (no visitar detalles) → más rápido
- **Estimación:** 48K ofertas ÷ 2000/día = **~24 días para catálogo completo**
- **Alternativa acelerada:** 8 sesiones/día, 30 páginas/sesión = **~10 días**

#### Fase B: Mantenimiento diario (día 3+)
- **Objetivo:** Recopilar solo ofertas nuevas
- **Volumen estimado:** ~500-1500 ofertas nuevas/día en jobup.ch
- **Ritmo:** 2-3 sesiones/día, ordenando por fecha publicación
- **Detección de "ya visto":** Parar cuando se encuentren ofertas ya en la DB
- **Duración:** ~30-60 min/día de actividad total

### 3.4 Diagrama de flujo
```
┌─────────────────────────────────────┐
│          SCHEDULER (Celery)         │
│  Lanza N sesiones/día espaciadas    │
└───────────────┬─────────────────────┘
                │
                ▼
┌─────────────────────────────────────┐
│         SESIÓN (Playwright)         │
│                                     │
│  1. Abrir browser con perfil real   │
│  2. Navegar a jobup.ch/en/jobs/     │
│  3. Aceptar cookies (si aparecen)   │
│  4. FOR page in range(max_pages):   │
│     a. Extraer __INIT__ JSON        │
│     b. Parsear ofertas del listado  │
│     c. ¿Oferta ya en DB? → STOP    │
│     d. Random: ¿click detalle?      │
│        → delay lectura → extraer    │
│     e. Scroll down suavemente       │
│     f. Click "siguiente página"     │
│     g. Sleep(random 20-45s)         │
│  5. Cerrar browser                  │
└───────────────┬─────────────────────┘
                │
                ▼
┌─────────────────────────────────────┐
│       PIPELINE EXISTENTE            │
│  normalize → dedup → save to DB     │
└─────────────────────────────────────┘
```

---

## 4. Diseño técnico

### 4.1 Nuevo archivo: `backend/scrapers/jobup.py`

```python
class JobupScraper(BaseScraper):
    SOURCE_NAME = "jobup"
    LISTING_URL = "https://www.jobup.ch/en/jobs/"
    NEEDS_PLAYWRIGHT = True
    FETCH_DETAILS = False          # Fase A: solo listado
    RATE_LIMIT_SECONDS = 30.0      # Media entre páginas (se randomiza)
    MAX_PAGES = 20                 # Por sesión
    PAGE_SIZE = 20

    # --- Stealth config (no heredar de BaseScraper) ---
    STEALTH_MODE = True
    PAGES_PER_SESSION = (15, 25)   # rango aleatorio
    DELAY_BETWEEN_PAGES = (20, 45) # segundos, rango aleatorio
    DELAY_DETAIL_READ = (10, 25)   # segundos si visita detalle
    DETAIL_CLICK_PROBABILITY = 0.3
```

### 4.2 Diferencias con scrapers normales

El scraper de jobup.ch **NO usa el flujo estándar** de `BaseScraper._scrape_with_playwright()`.
Necesita un override completo de `fetch_jobs()` porque:

1. **Delays aleatorios** (no fijos como `RATE_LIMIT_SECONDS`)
2. **Scroll suave** antes de paginar (simular lectura)
3. **Extracción de `__INIT__` JSON** en vez de parsear HTML
4. **Early stop** cuando encuentra ofertas ya conocidas
5. **Cookie consent** handling automático
6. **Sesiones con estado** (cookies persistentes durante la sesión)

```python
async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
    """Override completo — sesión stealth con comportamiento humano."""
    if not await self._pre_check():
        return []

    all_raw = await self._stealth_session(query)
    results = self._process_raw_jobs(all_raw)

    if results:
        await self._reset_compliance_blocks()

    return self._finalize_fetch(results)

async def _stealth_session(self, query: str) -> list[dict]:
    """Una sesión de navegación simulando usuario humano."""
    from playwright.async_api import async_playwright
    import random

    all_jobs = []
    max_pages = random.randint(*self.PAGES_PER_SESSION)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=self._browser_args(),
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=self._random_user_agent(),
            locale="en-CH",
            timezone_id="Europe/Zurich",
            geolocation={"latitude": 47.3769, "longitude": 8.5417},
            permissions=["geolocation"],
        )
        page = await context.new_page()

        try:
            # 1. Navigate to listing
            await page.goto(self.LISTING_URL, wait_until="networkidle")
            await self._handle_cookie_consent(page)
            await self._human_scroll(page)

            # 2. Paginate
            for pg in range(max_pages):
                jobs = await self._extract_init_json(page)

                if not jobs:
                    break

                # Early stop: if we've seen these jobs before
                known_count = self._count_known_jobs(jobs)
                if known_count > len(jobs) * 0.8:
                    logger.info("80%% known jobs — stopping early")
                    break

                all_jobs.extend(jobs)

                # Random delay (human reading time)
                delay = random.uniform(*self.DELAY_BETWEEN_PAGES)
                await asyncio.sleep(delay)

                # Smooth scroll + click next page
                await self._human_scroll(page)
                has_next = await self._click_next_page(page)
                if not has_next:
                    break

        finally:
            await browser.close()

    return all_jobs
```

### 4.3 Métodos auxiliares stealth

```python
def _browser_args(self) -> list[str]:
    """Chrome args que dificultan detección de headless."""
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--no-sandbox",
    ]

def _random_user_agent(self) -> str:
    """Rotar entre user agents reales de Chrome reciente."""
    agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ..."
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ...",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ...",
    ]
    return random.choice(agents)

async def _human_scroll(self, page) -> None:
    """Scroll suave simulando lectura humana."""
    for _ in range(random.randint(2, 5)):
        scroll_amount = random.randint(200, 600)
        await page.mouse.wheel(0, scroll_amount)
        await asyncio.sleep(random.uniform(0.5, 2.0))

async def _handle_cookie_consent(self, page) -> None:
    """Aceptar cookies si aparece el banner."""
    try:
        accept_btn = page.locator("button:has-text('Accept'), button:has-text('Akzeptieren')")
        if await accept_btn.is_visible(timeout=3000):
            await accept_btn.click()
            await asyncio.sleep(1)
    except Exception:
        pass  # No cookie banner — continue

async def _extract_init_json(self, page) -> list[dict]:
    """Extraer datos de ofertas del objeto __INIT__ embebido."""
    init_data = await page.evaluate("""
        () => {
            const scripts = document.querySelectorAll('script');
            for (const s of scripts) {
                if (s.textContent.includes('__INIT__')) {
                    const match = s.textContent.match(/__INIT__\\s*=\\s*(\\{.*?\\});/s);
                    if (match) return JSON.parse(match[1]);
                }
            }
            return null;
        }
    """)
    if not init_data:
        return []

    # Navigate nested structure to find job results
    results = (init_data
               .get("vacancy", {})
               .get("results", {})
               .get("main", {})
               .get("results", []))
    return results

async def _click_next_page(self, page) -> bool:
    """Click en botón de siguiente página."""
    try:
        next_btn = page.locator("a[rel='next'], button:has-text('Next')")
        if await next_btn.is_visible(timeout=3000):
            await next_btn.click()
            await page.wait_for_load_state("networkidle")
            return True
    except Exception:
        pass
    return False
```

### 4.4 Normalización

```python
def normalize_job(self, raw: dict) -> dict:
    """Normalizar datos del JSON __INIT__ al esquema del proyecto."""
    job_id = raw.get("id", "")
    title = raw.get("title", "").strip()
    company_data = raw.get("company", {})
    company = company_data.get("name", "").strip()
    location = raw.get("place", "").strip()
    url = f"https://www.jobup.ch/en/jobs/detail/{job_id}/"

    # Employment grade: e.g. [80, 100] → "80-100%"
    grades = raw.get("employmentGrades", [])
    employment_type = f"{grades[0]}-{grades[1]}%" if len(grades) == 2 else None

    # Publication date
    pub_date = raw.get("publicationDate")

    return {
        "hash": self.compute_hash(title, company, url),
        "source": self.SOURCE_NAME,
        "source_id": str(job_id),
        "title": title,
        "company": company,
        "location": location,
        "canton": extract_canton(location),
        "description": "",  # Solo disponible en detalle
        "description_snippet": "",
        "url": url,
        "remote": False,
        "tags": [],
        "logo": company_data.get("logoUrl"),
        "salary_min_chf": None,
        "salary_max_chf": None,
        "salary_original": None,
        "salary_currency": None,
        "salary_period": None,
        "language": None,
        "seniority": None,
        "contract_type": None,
        "employment_type": employment_type,
        "posted_at": pub_date,
    }
```

### 4.5 Early stop — detección de ofertas conocidas

```python
def _count_known_jobs(self, raw_jobs: list[dict]) -> int:
    """Count how many jobs from this batch are already in our DB.

    Uses an in-memory set of known hashes loaded at session start.
    """
    count = 0
    for raw in raw_jobs:
        title = raw.get("title", "")
        company = raw.get("company", {}).get("name", "")
        job_id = raw.get("id", "")
        url = f"https://www.jobup.ch/en/jobs/detail/{job_id}/"
        h = self.compute_hash(title, company, url)
        if h in self._known_hashes:
            count += 1
    return count
```

---

## 5. Scheduling: Sesiones distribuidas

### 5.1 Nuevo Celery task

No incluir en `fetch_scrapers` (el task general de scraping cada 6h).
Crear un **task independiente** con scheduling propio:

```python
# backend/tasks/jobup_tasks.py

@celery_app.task(name="tasks.jobup.stealth_session", bind=True,
                 soft_time_limit=2400, time_limit=3000)
def jobup_stealth_session(self) -> dict:
    """Run one stealth browsing session on jobup.ch."""
    return asyncio.run(_run_session())

async def _run_session():
    scraper = JobupScraper()
    # Load known hashes before session
    scraper._known_hashes = await _load_known_hashes()
    jobs = await scraper.fetch_jobs("")
    # Save via standard pipeline
    ...
```

### 5.2 Schedule (APScheduler → Celery Beat)

```python
# En el scheduler: 5 sesiones/día distribuidas
JOBUP_SESSION_HOURS = [8, 11, 14, 17, 20]  # Horas UTC+1 (horario suizo)

# Cada sesión se dispara con jitter aleatorio de ±30 min
# para evitar patrones detectables
```

### 5.3 Fase A vs Fase B automática

```python
async def _run_session():
    known_count = await _count_jobup_jobs_in_db()

    if known_count < 40000:
        # Fase A: crawl completo, sin early-stop
        scraper.EARLY_STOP_ENABLED = False
        scraper.MAX_PAGES = 30  # más páginas por sesión
    else:
        # Fase B: mantenimiento, ordenar por fecha, early-stop
        scraper.EARLY_STOP_ENABLED = True
        scraper.MAX_PAGES = 15
        scraper.SORT_BY = "date"  # ofertas más recientes primero
```

---

## 6. Integración con el proyecto

### 6.1 Archivos a crear/modificar

| Archivo | Acción | Descripción |
|---------|--------|-------------|
| `backend/scrapers/jobup.py` | **Crear** | Scraper stealth completo |
| `backend/tasks/jobup_tasks.py` | **Crear** | Celery task independiente |
| `backend/scrapers/__init__.py` | **Modificar** | Registrar JobupScraper (comentado por defecto) |
| `alembic/versions/xxx_seed_jobup_compliance.py` | **Crear** | Seed compliance row |
| `backend/config.py` | **Modificar** | Añadir settings JOBUP_* |

### 6.2 Config settings

```python
# config.py
JOBUP_ENABLED: bool = False                    # Deshabilitado por defecto
JOBUP_SESSIONS_PER_DAY: int = 5
JOBUP_SESSION_HOURS: list[int] = [8, 11, 14, 17, 20]
JOBUP_PAGES_PER_SESSION: tuple[int, int] = (15, 25)
JOBUP_DELAY_BETWEEN_PAGES: tuple[int, int] = (20, 45)
JOBUP_EARLY_STOP_THRESHOLD: float = 0.8       # 80% ofertas conocidas → stop
```

### 6.3 Compliance entry

```python
SourceCompliance(
    source_key="jobup",
    is_allowed=False,        # Requiere activación manual explícita
    method="scraping",
    robots_txt_ok=True,      # /en/jobs/ no está bloqueado en robots.txt
    tos_notes="TOS prohíben scraping. Solo uso personal. Activar manualmente.",
    auto_disable_on_block=True,
)
```

---

## 7. Medidas anti-detección

| Medida | Implementación |
|--------|----------------|
| **User-Agent rotation** | Pool de 5+ UAs de Chrome real (Win/Mac/Linux) |
| **Viewport realista** | 1920×1080 (no 800×600 headless default) |
| **Timezone/locale** | `Europe/Zurich`, `en-CH` |
| **Geolocation** | Zurich (47.37°N, 8.54°E) |
| **Anti-headless flags** | `--disable-blink-features=AutomationControlled` |
| **Cookie persistence** | Mantener cookies durante toda la sesión |
| **Scroll humano** | Random scroll amounts con micro-delays |
| **Delays aleatorios** | Distribución normal, no intervalos fijos |
| **Early stop** | Parar cuando 80% son ofertas conocidas |
| **Sesiones cortas** | 15-25 páginas máximo por sesión |
| **Horarios variables** | Jitter de ±30 min en cada sesión |
| **Sin parallelismo** | 1 sesión a la vez, nunca concurrent |

---

## 8. Limitaciones conocidas

1. **Sin descripción completa:** Solo datos del listado (título, empresa, ubicación, salario).
   Para descripciones completas habría que visitar detalles (mucho más lento).
2. **AWS WAF CAPTCHA:** Si se activa, la sesión debe abortar inmediatamente.
   El ComplianceEngine reportará el bloqueo y deshabilitará tras 3 consecutivos.
3. **Catálogo completo lento:** ~10-24 días para las ~48K ofertas iniciales.
4. **No apto para producción:** Solo uso personal. No incluir en builds públicos.

---

## 9. Consideraciones legales

- **robots.txt:** Las páginas de ofertas (`/en/jobs/`) **no están bloqueadas**
- **TOS:** Prohíben acceso automatizado — este scraper viola los TOS
- **Riesgo real (uso personal):** Prácticamente nulo a este volumen
- **Riesgo si se comercializa:** Significativo — acción legal probable
- **Recomendación:** Mantener `JOBUP_ENABLED=False` por defecto.
  Solo activar manualmente para uso personal. No incluir datos de jobup.ch
  en ningún servicio público.

---

## 10. Alternativas consideradas

| Alternativa | Pros | Contras |
|-------------|------|---------|
| **API oficial JobCloud** | Legal, completa, estable | Solo partners comerciales, costosa |
| **Sitemap crawling** | Más discreto | Solo URLs, sin datos estructurados |
| **RSS/Atom feed** | Ligero | No existe para jobup.ch |
| **Scraper directo (sin stealth)** | Más rápido, simple | Detectable, bloqueo probable |
| **→ Stealth Playwright** | Indetectable a bajo volumen | Lento, complejo, viola TOS |

---

## 11. Checklist de implementación

- [ ] Crear `backend/scrapers/jobup.py` con `JobupScraper`
- [ ] Implementar `_stealth_session()` con delays aleatorios
- [ ] Implementar `_extract_init_json()` — parsear `__INIT__` object
- [ ] Implementar `_human_scroll()` y `_handle_cookie_consent()`
- [ ] Implementar `_count_known_jobs()` para early-stop
- [ ] Implementar `normalize_job()` para formato `__INIT__`
- [ ] Crear `backend/tasks/jobup_tasks.py` con task independiente
- [ ] Añadir settings `JOBUP_*` en `config.py`
- [ ] Crear migración compliance seed
- [ ] Registrar en `scrapers/__init__.py` (comentado)
- [ ] Configurar schedule en Celery Beat (deshabilitado por defecto)
- [ ] Test manual: ejecutar 1 sesión y verificar extracción
- [ ] Verificar que AWS WAF CAPTCHA no se activa
- [ ] Monitorear logs durante 48h de prueba
