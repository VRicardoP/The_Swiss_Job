# Plan: Crawler Incremental Multi-Fuente para SwissJob

> **Estado:** Rediseñado - reemplaza el plan anterior de scraper stealth para jobup.ch  
> **Fecha:** 2026-07-03  
> **Eje central:** el volumen de peticiones depende del número de ofertas nuevas, no del número total de ofertas.

---

## 1. Objetivo

SwissJob necesita ampliar cobertura sin convertir el scraping en un proceso caro, frágil o legalmente problemático. El enfoque correcto no es navegar miles de ofertas, sino mantener cursores por fuente y consultar solo el tramo reciente de cada listado.

La regla de diseño es:

```text
requests_por_run ~= páginas_necesarias_hasta_encontrar_oferta_conocida
requests_por_run ~= 1 + ceil(ofertas_nuevas / page_size)
```

No debe depender de:

```text
requests_por_run ~= total_ofertas / page_size
```

Esto cambia el sistema de un crawler exhaustivo a un sincronizador incremental. El primer crawl puede necesitar una ventana de arranque controlada, pero el modo normal debe parar en cuanto encuentre identificadores ya conocidos.

---

## 2. Principios no negociables

1. **Incremental primero.** Toda fuente debe guardar un cursor: `last_seen_id`, `last_seen_posted_at`, `etag`, `last_modified`, sitemap `lastmod` o hash de la cabecera/listado.
2. **La fuente manda el método.** API, feed ATS, XML autorizado o sitemap permitido siempre tienen prioridad sobre HTML.
3. **HTML es último recurso.** Solo para fuentes con permiso suficiente, robots compatible y bajo volumen.
4. **No bypass.** No diseñar el sistema alrededor de evadir WAF, CAPTCHA, login, paywalls o controles anti-bot. Un 403/429/CAPTCHA es señal de parada y revisión.
5. **Conectores preparados no significan conectores activos.** jobs.ch/jobup.ch, LinkedIn, Indeed, Glassdoor y XING pueden existir en el catálogo técnico, pero deben arrancar deshabilitados hasta disponer de API, feed, partnership, export autorizado o revisión legal.
6. **Dedupe global obligatorio.** La misma vacante puede llegar por ATS, portal, agregador, newsletter y web corporativa.

---

## 3. Modelo de adquisición por capas

| Capa | Método | Uso en SwissJob | Huella | Activación |
|------|--------|-----------------|--------|------------|
| 0 | ATS público/API pública | Greenhouse, Lever, Ashby, SmartRecruiters, Workday cuando expone endpoints públicos | Muy baja | Por defecto si el endpoint es público y permitido |
| 1 | API/partner feed autorizado | JobCloud, LinkedIn, Indeed, Glassdoor, XING, Talent.com, Job-Room | Muy baja | Solo con credenciales/acuerdo |
| 2 | Import autorizado por usuario | emails de alertas, CSV/export, enlaces guardados, webhooks de cuentas propias | Baja | Opt-in del usuario |
| 3 | Sitemap/RSS permitido | URLs con `lastmod`, RSS de búsquedas guardadas, feeds de empresas | Baja | Si robots/TOS lo permiten |
| 4 | HTML público permitido | portales pequeños, webs corporativas, colegios, nichos | Media | Gated por `SourceCompliance` |
| 5 | Fuente restringida sin permiso | jobs.ch/jobup.ch, LinkedIn, Indeed, Glassdoor, XING si no hay acuerdo | Cero | No se consulta |

La arquitectura debe maximizar capas 0-2. La capa 4 queda para fuentes donde SwissJob ya tiene permiso operacional razonable y el valor justifica el coste.

---

## 4. Fuentes restringidas: cómo prepararlas sin activarlas indebidamente

Estas fuentes ya no deben tratarse como "prohibidas para siempre", sino como **fuentes restringidas con rutas de integración autorizables**. Eso permite modelarlas ahora y activarlas después sin rediseñar el sistema.

| Fuente | Estado por defecto | Ruta aceptable | Qué construir ahora |
|--------|--------------------|----------------|---------------------|
| JobCloud: jobs.ch / jobup.ch | `disabled` | API/partner, XML/interface oficial, acuerdo comercial, feed autorizado por empleador | `jobcloud_partner` provider con credenciales opcionales; sin scraper público |
| LinkedIn Jobs | `disabled` | LinkedIn Talent Solutions / Job Posting / Apply Connect si SwissJob llega a partner; import de alertas/enlaces del usuario | `linkedin_authorized` connector; ingest de URLs guardadas por usuario |
| Indeed | `disabled` | Indeed Partner APIs, Job Sync, Publisher plugin/feed aprobado, XML/ATS integration autorizada | `indeed_partner` connector; ingest de alertas o export autorizado |
| Glassdoor | `disabled` | API partner con partner id/key o canal aprobado | `glassdoor_partner` connector; no crawling de reviews/jobs |
| XING | `disabled` | XING e-recruiting feed/API, Apply With XING, partner approval | `xing_partner` connector; soporte para feed autorizado |

Cada una debe existir en `source_compliance` con:

```text
is_allowed = false
method = "partner_feed" | "api" | "manual_import"
robots_txt_ok = false/unknown hasta revisión
auto_disable_on_block = true
tos_notes = "Restricted source. Enable only with authorized API/feed/partner path."
```

La UI/admin debe poder mostrar estas fuentes como "preparadas", no como "rotas".

---

## 5. Cursor incremental

Cada fuente se divide en scopes. Un scope es una combinación estable como:

```text
source_key + query + location + language + category + remote_mode
```

Ejemplos:

```text
careerjet|teacher|Switzerland|fr|education|any
jobcloud_partner|software engineer|Geneva|en|tech|hybrid
greenhouse|company_slug=acme
```

### 5.1 Tabla propuesta: `source_cursors`

```text
id
source_key
scope_key
cursor_type                 # id | timestamp | etag | last_modified | sitemap_lastmod | hash
cursor_value                # valor serializado principal
high_watermark_posted_at
recent_source_ids_json      # ventana corta para early-stop y reorder
recent_content_hashes_json  # fallback si no hay ID estable
bootstrap_complete
last_run_at
last_success_at
last_empty_at
next_run_at
avg_new_jobs_per_run
avg_pages_per_run
consecutive_empty_runs
consecutive_errors
created_at
updated_at
```

`recent_source_ids_json` no sustituye la tabla `jobs`; es una cache pequeña para parar rápido cuando el portal reordena ofertas o mezcla patrocinadas.

### 5.2 Semántica del cursor

| Tipo | Cuándo usarlo | Parada |
|------|---------------|--------|
| `id` | IDs monotónicos o URLs con ID estable | parar al encontrar ID ya visto |
| `timestamp` | listados ordenados por fecha real | parar cuando `posted_at <= high_watermark` y IDs ya vistos |
| `etag` / `last_modified` | APIs/feeds HTTP compatibles | no procesar si 304 o cabecera sin cambios |
| `sitemap_lastmod` | sitemaps por fecha | solo abrir URLs con `lastmod` nuevo |
| `hash` | fuentes sin ID ni fecha fiable | parar cuando N hashes consecutivos ya existen |

---

## 6. Algoritmo de crawl

### 6.1 Arranque controlado

El bootstrap no debe intentar absorber todo un portal si no es necesario. Para SwissJob basta con una ventana útil:

| Fuente | Bootstrap recomendado |
|--------|-----------------------|
| API con filtros | 7-30 días o primeras 5 páginas por scope |
| ATS público | todos los jobs activos de la empresa, normalmente una petición |
| Sitemap | URLs con `lastmod` reciente; límite por día |
| HTML permitido | 3-10 páginas por scope, según `page_size` y valor de fuente |
| Fuente restringida | 0 peticiones |

### 6.2 Mantenimiento normal

Pseudo-flujo:

```text
for source_scope in due_scopes:
    load cursor
    page = 1
    new_jobs = []
    seen_known_streak = 0

    while page <= source.max_pages_per_run:
        batch = fetch_page_or_feed(source_scope, page, cursor)

        if unchanged(batch):        # 304, same etag, same list hash
            break

        for raw_job in batch.jobs:
            identity = extract_source_identity(raw_job)

            if already_seen(identity):
                seen_known_streak += 1
                if should_stop(seen_known_streak, page, source):
                    stop scope
            else:
                new_jobs.append(raw_job)
                seen_known_streak = 0

        if batch.has_no_next_page:
            break

        page += 1

    normalize -> dedup -> save
    update cursor with newest successfully saved job
    update scheduler metrics
```

### 6.3 Condición de parada

La condición debe combinar ID, fecha y racha de conocidos:

```text
stop if:
  - current_id in recent_source_ids
  - and current_posted_at <= high_watermark_posted_at + reorder_tolerance
  - or known_streak >= source.known_streak_stop_threshold
```

Valores iniciales:

```text
known_streak_stop_threshold = 5
reorder_tolerance = 48h
max_pages_per_run = min(10, configured_source_limit)
```

Esto evita cortar demasiado pronto cuando hay ofertas patrocinadas, reposts o ordenamientos no estrictamente cronológicos.

---

## 7. Presupuesto dinámico de peticiones

Cada fuente debe tener un presupuesto por run calculado desde su histórico:

```text
expected_pages = ceil(max(avg_new_jobs_per_run, 1) / page_size) + safety_page
max_pages_this_run = clamp(expected_pages, min_pages=1, max_pages=source_cap)
```

Reglas:

- Si tres runs seguidos devuelven cero ofertas nuevas, reducir frecuencia.
- Si un run encuentra nuevas ofertas en la última página permitida, subir una página de margen en el siguiente run.
- Si aparece 403, 429, CAPTCHA, login inesperado o soft-block, abortar y reportar a `ComplianceEngine`.
- Si la fuente soporta `ETag` o `Last-Modified`, hacer petición condicional antes de procesar contenido.

Resultado esperado:

| Situación | Peticiones |
|-----------|------------|
| No hay cambios | 0-1 |
| 1-20 nuevas con `page_size=20` | 1 |
| 21-40 nuevas | 2 |
| Portal con 50K ofertas pero 12 nuevas | 1 |

---

## 8. Diseño en SwissJob

### 8.1 Componentes nuevos

| Componente | Responsabilidad |
|------------|-----------------|
| `SourceCursor` model | Persistir cursores por fuente/scope |
| `CursorStore` service | Leer, actualizar y bloquear cursores durante un run |
| `IncrementalFetchResult` schema | Devolver `jobs`, `cursor_update`, `pages_read`, `stop_reason` |
| `SourceRegistry` ampliado | Distinguir `api`, `partner_feed`, `ats_feed`, `manual_import`, `html_scraping` |
| `CrawlerBudgetService` | Calcular `next_run_at`, `max_pages_this_run` y backoff |
| `SourceCompliance` ampliado | Mantener kill-switch y estado legal/operacional |

### 8.2 Cambios en providers/scrapers

Los providers actuales pueden seguir devolviendo jobs, pero el contrato debería evolucionar hacia:

```python
class IncrementalProvider:
    source_key: str
    method: str

    async def fetch_incremental(
        self,
        scope: SourceScope,
        cursor: SourceCursor,
        budget: CrawlBudget,
    ) -> IncrementalFetchResult:
        ...
```

Para compatibilidad:

```text
BaseJobProvider.fetch_jobs()
  -> wrapper legacy
  -> crea scope por defecto
  -> llama fetch_incremental si existe
```

### 8.3 Scheduling

El scheduler actual lanza providers/scrapers por intervalos fijos. El nuevo modelo debería lanzar scopes vencidos:

```text
tasks.sources.fetch_due_scopes
  - SELECT source_cursors WHERE next_run_at <= now()
  - lock por scope
  - calcular budget
  - ejecutar provider
  - guardar jobs
  - actualizar cursor y next_run_at
```

Frecuencias iniciales:

| Tipo | Frecuencia base |
|------|-----------------|
| ATS público | 6-24h |
| API/agregador con cuota | según cuota; 6-12h |
| Job-Room/SECO | 6-12h si credenciales |
| HTML permitido | 12-24h |
| Watchlist de empresas/colegios | 6-24h según prioridad |
| Fuente restringida deshabilitada | nunca |

---

## 9. Normalización y deduplicación

Para que el crawler incremental sea fiable, el ID de fuente no basta. El pipeline debe construir varias identidades:

```text
source_identity = source_key + source_job_id
canonical_url_identity = canonicalized_url
semantic_identity = hash(normalize(title) + normalize(company) + normalize(location))
content_identity = fuzzy_hash(description_snippet or description)
```

Orden de decisión:

1. Si `source_identity` existe, actualizar metadata y cursor.
2. Si `canonical_url_identity` existe, unir como duplicado.
3. Si `semantic_identity` coincide con alta confianza, marcar duplicate_of.
4. Si solo coincide parcialmente, guardar como candidato a revisión/dedupe fuzzy.

Esto es crítico al habilitar fuentes restringidas por vías autorizadas, porque Indeed, LinkedIn, JobCloud y los ATS pueden publicar la misma oferta.

---

## 10. Conectores ATS públicos

Estos conectores deben tener prioridad porque entregan datos estructurados con muy pocas peticiones:

| ATS | Cursor recomendado | Patrón |
|-----|--------------------|--------|
| Greenhouse | `updated_at` + `id` | listar jobs del board; `content=true` si hace falta descripción |
| Lever | `createdAt`/`updatedAt` + posting ID | JSON público por company site |
| Ashby | job ID + updated timestamp si disponible | API/job board endpoint |
| SmartRecruiters | posting ID + `releasedDate`/`updatedDate` | postings API |
| Workday | requisition ID + fecha | variable por tenant; usar solo endpoints publicados |

Un único run por empresa suele costar 1 petición. Para 500 empresas con ATS, el volumen diario puede ser cientos de peticiones, no decenas de miles.

---

## 11. Fuentes restringidas: conectores objetivo

### 11.1 JobCloud (`jobs.ch`, `jobup.ch`)

No crear scraper público de jobs.ch/jobup.ch. Crear un provider deshabilitado que solo acepte estas entradas:

- credenciales/API de JobCloud si se obtiene acuerdo;
- XML/interface oficial autorizado;
- feed de empleador cuando SwissJob actúe como ATS/partner;
- import manual de enlaces guardados por el usuario.

Cursor:

```text
source_job_id = external advert ID / INSERATID / partner job id
cursor_type = timestamp or id
```

### 11.2 LinkedIn

No depender de scraping autenticado ni búsqueda pública automatizada. Preparar:

- connector para Job Posting / Apply Connect si hay partner access;
- import de alertas de empleo recibidas por email;
- import manual de saved jobs o URLs pegadas por el usuario;
- enriquecimiento mínimo: guardar URL, título visible, empresa, ubicación y fecha si llegan por canal autorizado.

Cursor:

```text
source_job_id = LinkedIn job URN/id cuando venga por API
fallback = canonical URL hash para imports manuales
```

### 11.3 Indeed

Preparar provider para:

- Partner APIs / Job Sync;
- Publisher JavaScript/plugin o feed aprobado si aplica al modelo de SwissJob;
- XML/ATS integration autorizada;
- email alerts/import usuario.

No usar endpoints internos ni crawling de resultados.

### 11.4 Glassdoor

Preparar connector con partner ID/key y límites estrictos. Separar dos dominios:

- jobs autorizados;
- company/reviews/salary data solo si la licencia lo permite.

Por defecto no importar reviews ni salarios desde HTML.

### 11.5 XING

Preparar connector para:

- XING e-recruiting feed;
- XING e-recruiting API;
- Apply With XING si SwissJob integra candidatura;
- import autorizado del usuario.

Mantener el estado `disabled` hasta aprobación.

---

## 12. Compliance y estados operativos

La lista actual de "prohibidos" debe evolucionar a estados:

| Estado | Significado |
|--------|-------------|
| `allowed` | Puede ejecutarse automáticamente |
| `restricted_ready` | Conector existe, pero falta credencial/acuerdo |
| `manual_only` | Solo import de usuario/email/CSV |
| `blocked` | Hubo bloqueo técnico; no reintentar sin revisión |
| `forbidden` | No hay ruta aceptable; no construir conector |

En la base actual puede representarse inicialmente con `is_allowed=false` y `tos_notes`, pero conviene añadir un campo futuro `status`.

Propuesta:

```text
status: allowed | restricted_ready | manual_only | blocked | forbidden
authorization_type: public | partner | user_import | internal | none
authorization_reference: URL/contrato/ticket interno
reviewed_at
reviewed_by
```

---

## 13. Observabilidad

Métricas mínimas:

```text
source_requests_total{source,scope}
source_new_jobs_total{source,scope}
source_pages_per_run{source,scope}
source_stop_reason_total{source,reason}
source_cursor_lag_seconds{source,scope}
source_duplicate_rate{source}
source_compliance_blocks_total{source,status_code}
```

Stop reasons:

```text
known_id
known_hash_streak
etag_not_modified
last_modified_not_modified
page_limit
empty_page
compliance_block
auth_missing
source_disabled
parse_error
```

Estas métricas demuestran si el sistema cumple el objetivo: menos páginas cuando hay menos ofertas nuevas.

---

## 14. Plan de implementación

> Checklist sincronizado con el código el 2026-07-18. Donde la implementación
> difiere del diseño original se anota cómo se materializó.

### Fase 1: Documento y modelo

- [x] Reemplazar el plan de scraper stealth por diseño incremental.
- [x] Crear modelo `SourceCursor` (`models/source_cursor.py` — versión simplificada: identidades por URL en `recent_identities`, métricas EMA; sin columnas etag/next_run_at, ver Fase 5).
- [x] Crear `CursorStore` (`services/cursor_store.py`).
- [x] Añadir migración para cursores (`alembic/versions/d4e6f8a0b2c1_add_source_cursors.py`).
- [x] Estado extendido de fuente en `SourceCompliance` (migraciones `e6f8a0b2c4d3_seed_restricted_compliance` + `f6a8b9c1d2e3_source_compliance_last_success`).

### Fase 2: Motor incremental

- [ ] ~~Crear `IncrementalFetchResult`~~ — descartado: en lugar del contrato nuevo, el pipeline inyecta `_known_urls` en el provider y lee `_stop_reason` (diseño más simple, sin romper `BaseJobProvider`).
- [x] Wrapper incremental compatible con `BaseJobProvider` — materializado como early-stop en `BaseScraper` (`_page_all_known()` en los caminos httpx, Playwright y scroll AJAX de schuljobs).
- [x] Implementar `CrawlerBudgetService` (`services/crawler_budget.py`, 2026-07-18): páginas por run según `avg_new_jobs_per_run` (§7) + backoff de frecuencia tras `CRAWLER_BUDGET_EMPTY_RUNS_THRESHOLD` runs vacíos. Inyectado por `tasks/scraping_tasks.py`; el scraper lo respeta vía `_pages_budget()`.
- [ ] Cambiar scheduler para scopes vencidos, no solo intervalos fijos globales — parcial: el schedule sigue siendo fijo (cosecha diaria / cada 6h), pero `CrawlerBudgetService.should_run` salta fuentes sin novedades dentro de cada run (backoff por fuente).
- [x] Registrar `stop_reason` en logs y métricas — `_stop_reason` ("known_page") + métricas EMA en el cursor (`avg_new_jobs_per_run`, `avg_pages_per_run`, `consecutive_empty_runs`).

### Fase 3: Fuentes de alta eficiencia

- [ ] Implementar conectores ATS públicos: Greenhouse, Lever, Ashby, SmartRecruiters (existe un caso Workday ad-hoc: `scrapers/swiss_schools_isp.py`).
- [ ] Adaptar Jooble/Careerjet/Adzuna/JSearch a cursores cuando la API lo permita (hoy el cursor solo se inyecta en scrapers).
- [ ] Añadir import por email/CSV/URL para fuentes restringidas.
- [x] Mantener HTML permitido solo para fuentes pequeñas y watchlist (scrapers actuales: portales de nicho + watchlist `swiss_schools_*`).

### Fase 4: Fuentes restringidas preparadas

- [x] Seed de `source_compliance` para las 5 fuentes restringidas con `is_allowed=false` (`e6f8a0b2c4d3_seed_restricted_compliance.py`).
- [x] Providers stub que devuelven `auth_missing` sin credenciales (`providers/restricted.py` — 0 peticiones, nunca scraping).
- [ ] Añadir UI/admin para mostrar "preparado, pendiente de autorización".
- [x] Documentar procedimiento de activación por fuente (docstrings de `providers/restricted.py`: ruta autorizada + credencial por fuente).

### Fase 5: Optimización

- [x] Recalcular frecuencia — vía backoff de `CrawlerBudgetService.should_run` (runs vacíos consecutivos duplican el intervalo exigido, tope `CRAWLER_BUDGET_BACKOFF_MAX_MULTIPLIER`).
- [ ] Usar `ETag`/`Last-Modified` donde exista.
- [ ] Usar sitemap `lastmod` antes de abrir detalles.
- [ ] Medir `requests_per_new_job` por fuente (proxy actual: `avg_pages_per_run` vs `avg_new_jobs_per_run` en el cursor).
- [ ] Desactivar fuentes con bajo valor o alto coste (manual hoy: p.ej. medjobs deshabilitado por Cloudflare).

---

## 15. Criterios de éxito

El rediseño está funcionando si:

- Una fuente sin cambios cuesta 0-1 peticiones por run.
- Una fuente con pocas novedades no pagina más allá de la primera página.
- `requests_per_new_job` baja con el tiempo.
- Las fuentes restringidas aparecen modeladas pero no se ejecutan sin autorización.
- Los bloqueos no generan reintentos agresivos: se convierten en estado de compliance.
- La deduplicación evita que una misma vacante aparezca varias veces al llegar desde JobCloud, LinkedIn, Indeed, ATS y web corporativa.

---

## 16. Referencias oficiales revisadas

- JobCloud Technical Solutions: https://www.jobcloud.ch/c/en/technical-solution/xml-fields/
- LinkedIn Talent Solutions / Job Posting API: https://learn.microsoft.com/en-us/linkedin/talent/job-postings/api/overview
- Indeed Partner Docs: https://docs.indeed.com/
- Indeed Apply / Job Sync: https://docs.indeed.com/indeed-apply/
- Glassdoor API documentation: https://www.glassdoor.com/developer/index.htm
- XING Job Integration: https://dev.xing.com/partners/job_integration
- Greenhouse Job Board API: https://developers.greenhouse.io/job-board.html

---

## 17. Conclusión

El sistema no debe intentar ganar por volumen ni por evasión. Debe ganar por arquitectura: cursores, fuentes estructuradas, permisos claros, dedupe fuerte y presupuesto dinámico.

La frase que debe guiar cada cambio del crawler es:

> El volumen de peticiones depende del número de ofertas nuevas, no del número total de ofertas.
