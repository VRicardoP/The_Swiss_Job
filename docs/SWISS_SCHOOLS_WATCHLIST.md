# Watchlist colegios suizos — Hallazgos y documentación técnica

> Documenta el subsistema de vigilancia de candidaturas a 17 colegios
> internacionales en Suiza. Cubre la investigación por portal, las
> estrategias de scraping aplicadas, la arquitectura de la action layer
> (state machine + cartas + calendar) y las decisiones de diseño.
>
> Última actualización: 2026-05-29.

---

## 1. Lista de colegios y estado

| ID | Colegio | Ciudad | Tier | Policy | Scraper | Estado |
|----|---------|--------|------|--------|---------|--------|
| `champittet_nyon`    | Collège Champittet                 | Nyon                  | A | portal_nord_anglia    | `swiss_schools_nae`      | ✅ activo |
| `beausoleil_villars` | Collège Alpin Beau Soleil          | Villars-sur-Ollon     | A | portal_nord_anglia    | `swiss_schools_nae`      | ✅ activo |
| `lcis_aubonne`       | La Côte International School       | Aubonne               | A | portal_nord_anglia    | `swiss_schools_nae`      | ✅ activo |
| `mosaic_geneva`      | Mosaic Ecole                       | Geneva                | C | portal_workday        | `swiss_schools_isp`      | ✅ activo |
| `ges_versoix`        | Geneva English School              | Versoix               | B | portal_successfactors | `swiss_schools_inspired` | ✅ activo |
| `stgeorges_montreux` | St. George's International School  | Montreux              | A | portal_successfactors | `swiss_schools_inspired` | ✅ activo |
| `zis_zurich`         | Zurich International School        | Zurich                | A | portal_only           | `swiss_schools_zis`      | ✅ activo |
| `isb_basel`          | International School Basel         | Basel                 | A | direct_email_ok       | `swiss_schools_isb`      | ✅ activo |
| `ecolint_geneva`     | International School of Geneva     | Geneva                | A | portal_only           | `swiss_schools_ecolint`  | ✅ activo |
| `hautlac_stlegier`   | Haut-Lac Bilingual School          | Saint-Légier          | B | direct_email_ok       | `swiss_schools_hautlac`  | ✅ activo |
| `iscs_zug`           | ISCS Central Switzerland           | Cham/Zug              | B | direct_email_ok       | `swiss_schools_iscs`     | ✅ activo |
| `riviera_montreux`   | Ecole Riviera                      | Montreux              | C | direct_email_ok       | —                        | ⛔ manual |
| `verbier_vis`        | Verbier International School       | Verbier               | C | direct_email_ok       | —                        | ⛔ manual (via TES) |
| `isr_buchs`          | International School Rheintal      | Buchs                 | B | portal_only           | —                        | ⛔ manual |
| `bsb_bern`           | British School Bern                | Bern                  | C | direct_email_director | —                        | ⛔ manual |
| `lagarenne_villars`  | La Garenne International School    | Villars-sur-Ollon     | C | direct_email_ok       | —                        | ⛔ manual |
| `wisdomtree_pully`   | Wisdom Tree Education              | Pully                 | C | manual                | —                        | ⛔ manual |

**Cobertura automática: 11/17 colegios.** Los 6 restantes están documentados
como `manual` con sus razones técnicas (ver §3.6).

---

## 2. Estrategias técnicas por portal

### 2.1 Nord Anglia Education central (`swiss_schools_nae`)

- **Cubre**: Champittet, Beau Soleil, La Côte Aubonne.
- **Endpoint**: `https://careers.nordangliaeducation.com/search/?q=<keyword>`
- **Tecnología**: HTML estático (Lumesse / jobs2web).
- **Estructura**: `li.job-tile` por vacante.
  - Título: `a.jobTitle-link`
  - Campo escuela: `div[id$="-desktop-section-customfield3-value"]`
  - Ciudad: `div[id$="-desktop-section-city-value"]`
- **Trick descubierto**: el campo "School" en customfield3 NO tiene el
  nombre completo del colegio. Por ejemplo el alpino aparece como
  "Collège Beau Soleil" (sin "Alpin"). El filtro canónico está hardcoded
  en `_SCHOOL_CANONICAL` dentro del scraper.
- **Volumen actual**: 21 jobs entre los 3 colegios.

### 2.2 ISP Workday API (`swiss_schools_isp`)

- **Cubre**: Mosaic Ecole.
- **Endpoint**: `POST https://internationalschools.wd3.myworkdayjobs.com/wday/cxs/internationalschools/ISPCareers/jobs`
- **Tecnología**: API JSON pública de Workday. Cuerpo:
  ```json
  {"appliedFacets":{},"limit":20,"offset":0,"searchText":"mosaic"}
  ```
- **Respuesta**: `{"total":N, "jobPostings":[{title, externalPath, locationsText, bulletFields:[JR_ID]}]}`
- **URL detalle**: `https://internationalschools.wd3.myworkdayjobs.com/en-US/ISPCareers{externalPath}`
- **Filtro**: por `locationsText` que contiene "Mosaic School / Ecole Mosaic, Switzerland, Geneva".
- **Volumen actual**: 10 jobs.

### 2.3 Inspired SuccessFactors (`swiss_schools_inspired`)

- **Cubre**: Geneva English School + St. George's Montreux.
- **Endpoint**: `https://jobs.inspirededu.com/search/?q=<keyword>`
- **Tecnología**: HTML estático (misma plantilla Lumesse que NAE), pero
  con nombres de campo distintos:
  - Escuela: `facility`
  - Localización: `location`
  - Tipo contrato: `shifttype`
- **Filtro canónico**:
  - "Geneva English" → `Geneva English School`
  - "george" → `St. George's International School`
- **Volumen actual**: 11 jobs.

### 2.4 ZIS — Zurich International School (`swiss_schools_zis`)

- **Endpoint**: `https://www.zis.ch/one-zis-community/employment` (NO el
  portal SchoolSpring directamente).
- **Por qué no SchoolSpring**: `zurichinternational.schoolspring.com/`
  es SPA React detrás de Incapsula WAF — anti-bot fuerte, requiere
  Playwright. Y aun así, la página /employment del propio ZIS expone
  los enlaces con título visible.
- **Pattern**: cada vacante es un `<a>` con `href` que matchea
  `schoolspring.com/?jobid=N`. El texto del enlace es el título.
- **Volumen actual**: 4 jobs.

### 2.5 ISB — International School Basel (`swiss_schools_isb`)

- **Endpoint**: `https://www.isbasel.ch/connect/news/?board=employment-public-job-postings`
- **Tecnología**: Finalsite CMS, posts en `div.fsPostElement`.
- **Pattern**: cuando hay vacantes, cada post es `article.fsArticle` o
  `div.fsPost` con `a.fsPostLink`. Si está vacío, contiene
  `.fsElementEmpty` con texto "No post to display."
- **Volumen actual**: 0 jobs (board vacío hoy). El scraper está listo
  para cuando publiquen.

### 2.6 Ecolint — International School of Geneva (`swiss_schools_ecolint`)

- **Endpoint**: `https://www.ecolint.ch/en/job-opportunities`
- **Tecnología**: Drupal nativo, sin ATS externo.
- **Estructura**: `article.node-job-teaser` con:
  - Título: `span.f-n-title`
  - Detalle: `a[href*="/about/careers/"]`
- **Paginación**: `?page=N` (Drupal-style, 0-indexed). MAX_PAGES=5.
- **Volumen actual**: 15 jobs (es el de mayor volumen: 4500 alumnos,
  1250 staff, 3 campus).

### 2.7 Haut-Lac Bilingual School (`swiss_schools_hautlac`)

- **Endpoint**: `https://info.haut-lac.ch/jobs-and-career` (subdominio
  HubSpot CMS).
- **Pattern**: cada vacante es un widget `div.hs_cos_wrapper_type_rich_text`.
  El título es el primer `<h3>` con un `<span>` con
  `style="background-color: ..."`. Hay que filtrar h3 subapartado tipo
  "Profile", "Mission", "Responsibilities" para no inflar el contador.
- **Volumen actual**: 2 jobs.

### 2.8 ISCS — International School of Central Switzerland (`swiss_schools_iscs`)

- **Endpoint**: `https://iscs-zug.ch/employment/`
- **Pattern**: HTML estático con `<li><strong>JOB TITLE</strong></li>`.
  Filtros: longitud <200 caracteres, contiene una keyword
  (TEACHER / COORDINATOR / PROGRAMME / ACADEMY / DIRECTOR / PRINCIPAL /
  ASSISTANT), tiene al menos un `<strong>`.
- **Volumen actual**: 2 jobs.

---

## 3. Hallazgos por colegio (research del 2026-05-29)

### 3.1 Tier A — alta prioridad

- **Ecolint Geneva**: el más grande (4500 alumnos, 1250 staff, 3 campus).
  Solo aceptan candidaturas online; rechazan unsolicited postal/email.
  Pide cover + CV + 3 referencias. Director People & Culture conocido:
  **Soizic Le Clère**.
- **ZIS Zurich**: 1250+ alumnos. **Estricto: no email ni postal, solo
  portal**. Email HR: `applications@zis.ch` (solo queries, no
  candidaturas). Director: **Elsa Hernández-Donohue**.
- **ISB Basel**: acepta candidaturas año redondo para substitute/tutor.
  Prioridad CH/EU permit. HR: `recruitment@isbasel.ch`. Hoy 0 vacantes
  publicadas en el board.
- **Champittet** (Nord Anglia): campus Pully + Nyon. Email general:
  `nyon@champittet.ch`. Spontaneous vía "Share Your Profile" en NAE.
- **St. George's Montreux** (Inspired): ~400 alumnos boarding+day.
  Spontaneous expreso en portal SuccessFactors. **Está en Montreux, no
  en Grindelwald**: el doc original mencionaba un colegio británico en
  Grindelwald que no existe — St George's es el más probable candidato
  geográficamente cercano (lo más cercano a Grindelwald sería Lyceum
  Alpinum Zuoz o Aiglon Villars, ninguno con presencia británica clara).
- **La Côte Aubonne**: **YA NO es Cognita** — pertenece a Nord Anglia
  desde hace tiempo. `lcis.ch` redirige a nordangliaeducation.com.
- **Beau Soleil Villars**: boarding alpino prestigioso. Nord Anglia.
  Su web propia (`beausoleil.ch/careers`) duplica algunos posts del
  portal central; los manejamos solo desde el central para evitar dedup.

### 3.2 Tier B — media prioridad

- **GES Versoix** (Inspired): ~350 alumnos. Anuncia también en TES.com.
- **Haut-Lac** (HubSpot): ~600 alumnos bilingüe FR/EN. Pide
  CV+diplomas+experiencia+referencias en aplicación. Email:
  `jobs@haut-lac.ch`.
- **ISR Buchs** (Tier B, manual): SPA AbaServices, requiere form
  obligatorio (NO acepta CV solo). Head of HR: **Rene Sprecher**.
- **ISCS Zug**: Cambridge International / British. Pide PDF único con
  cover+CV+3 referencias. Solo permit CH válido. Email:
  `recruitment@iscs-zug.ch`.

### 3.3 Tier C — baja prioridad

- **Mosaic Geneva**: ISP via Workday. Email general:
  `info@ecolemosaic.ch`. Pequeña, rango 3-13 años.
- **Riviera Montreux**: pequeña bilingüe FR/EN. Email general:
  `info@ecole-riviera.ch`. No expone jobs en HTML estático.
- **Verbier**: muy pequeña alpina. Acepta spontaneous SIN vacante.
  Email: `info@vischool.ch`. Sus vacantes aparecen en TES.com (ya
  cubierto via dedup).
- **British School Bern**: locally owned. **Sin email HR público**.
  Anuncian via Facebook. Contacto via formulario web → policy
  `direct_email_director`.
- **La Garenne Villars**: boarding alpino ~180 alumnos. Email:
  `hr@la-garenne.ch`. **Background check Swiss obligatorio**. El home
  devuelve 403 con User-Agent simple → necesita UA real o Playwright.
- **Wisdom Tree Pully**: centro homeschooling fundado 2023. **Sin HR**.
  Publica solo en jobs.ch / jobup.ch (portales prohibidos por
  CLAUDE.md). Founder: **Eamon Sharkawi**. Marcado `manual` permanente.

### 3.4 Colegios sin scraper viable (los 6 manuales)

| Colegio | Motivo |
|---------|--------|
| Riviera Montreux  | No expone vacantes en HTML estático de su página /careers. Aplicar manualmente al email. |
| Verbier (VIS)     | Sus vacantes públicas están en `tes.com/jobs/vacancy/*` — ya las cubre el scraper `tes`. Crear scraper propio sería redundante. |
| ISR Buchs         | SPA AbaServices (`app.jobportal.abaservices.ch`) con shell de 893 bytes. Requeriría Playwright para ~1 vacante actual. Skip por relación coste/beneficio. |
| BSB Bern          | Sin listado público de vacantes. Anuncian via Facebook. Sin email HR público. |
| La Garenne        | Anti-bot 403 con UA simple. Requeriría Playwright o cabeceras de browser real. |
| Wisdom Tree       | Publica solo en jobs.ch/jobup.ch que están prohibidos por CLAUDE.md. |

### 3.5 Grindelwald — colegio no identificado

El doc original (`school_job_monitor_architecture.md`) menciona un
"colegio británico de la localidad" en Grindelwald. **No existe**:
ningún colegio británico/internacional en Grindelwald aparece en
research. El usuario confirmó que no tiene el nombre exacto. Se descarta
de la watchlist hasta nueva info.

### 3.6 Política de candidatura por colegio

Esta tabla es la pieza clave para la **action layer**:

| Policy | Significado | Acción al detectar vacante |
|--------|-------------|----------------------------|
| `direct_email_ok`        | Email HR público acepta candidaturas | Plantilla → email + adjunto |
| `direct_email_director`  | Dirigirse al director/founder por nombre | Plantilla → buscar contacto manual |
| `portal_only`            | Solo via portal/formulario; no email | Plantilla → abrir portal + recordatorio |
| `portal_nord_anglia`     | Vía portal central NAE | Plantilla → "Share Your Profile" URL |
| `portal_workday`         | Workday del grupo | Plantilla → URL Workday del job |
| `portal_successfactors`  | Inspired SuccessFactors | Plantilla → URL SF del job |
| `manual`                 | Sin política clara | Mostrar contacto humano |

Tres categorías de cara al usuario:
1. **Email directo** (8 colegios): Mosaic, Riviera, ISB, ISCS, La Garenne,
   GES (vía Inspired portal), Verbier, Haut-Lac.
2. **Portal externo** (8 colegios): ZIS, Ecolint, ISR, Champittet,
   La Côte, Beau Soleil, St George's, Mosaic.
3. **Manual / sin contacto público** (1 colegio): Wisdom Tree.

---

## 4. Arquitectura de la action layer

### 4.1 Bypass de categoría per-user

El classifier de jobs sigue marcando docencia como categoría `H` (×0.15
en multiplicador). Pero si `UserProfile.watchlist_schools_enabled=True`
y `job.source` empieza por `swiss_schools_`, el multiplicador se
sobrescribe a 1.0. Implementado en
[`backend/services/match_service.py`](../backend/services/match_service.py) en
`_category_multiplier_for(job, profile)`.

Razón: permite que el sistema siga penalizando docencia en general (la
preferencia del perfil Alicia Moore) pero abra la puerta solo para los
17 colegios concretos vigilados.

Migración: `c3d4e5f6a8b9_add_watchlist_schools_enabled` activa la flag
por defecto solo para usuarios existentes a esa fecha (Alicia).

### 4.2 State machine de candidatura

Implementado como columna `application_status` en `match_results`. Estados:

```
detected → reviewed → drafted → sent → awaiting
        → followup_due → interview → closed_positive | closed_negative
```

Persistencia: cuando el pipeline de matching se vuelve a ejecutar, el
estado de un match existente se preserva si `application_status != "detected"`.
Lo mismo con `draft_letter`. Ver
[`_save_results()`](../backend/services/match_service.py).

Endpoints: `POST /api/v1/watchlist/match/{job_hash}/status`.

### 4.3 Urgency scorer

Implementado en
[`backend/services/urgency_scorer.py`](../backend/services/urgency_scorer.py).
Devuelve 0-100, capado.

```
+30  Colegio tier A
+15  Colegio tier B
+15  Job publicado en últimas 48 h
+10  Job publicado en últimos 7 días
+20  "immediate"/"as soon as possible"/"urgent" en descripción
+10  Deadline explícita en < 7 días
-10  Policy "portal_only" (más fricción)
```

Solo aplica a jobs de la watchlist (otros devuelven 0). Se persiste en
`match_results.urgency_score` y se muestra como badge en `MatchCard`
cuando ≥ 30.

### 4.4 Plantillas de carta A/B

Plantillas placeholder en
[`backend/services/letter_generator.py`](../backend/services/letter_generator.py).
**Las plantillas reales las aportará el usuario más adelante.**

- **Plantilla A**: formal/urbano — colegios grandes en ciudad.
- **Plantilla B**: cálido/personal — boarding, alpinos, pequeños.

Estructura común (4 párrafos):
1. Apertura + motivo de la candidatura.
2. Cualificaciones relevantes del perfil.
3. **Marcador literal `[REVISAR: añadir referencia específica al colegio]`** — el doc original es explícito en NO automatizar este párrafo.
4. Cierre + disponibilidad.

La selección A vs B viene de `WatchedSchool.template_id`. Override
posible en el body del endpoint.

Idioma: detectado del anuncio (EN/FR/DE). Si Groq no está disponible
hay fallback que rellena un esqueleto mínimo.

### 4.5 Calendar export (.ics)

Endpoint `GET /api/v1/watchlist/match/{job_hash}/calendar.ics`.
Genera 2 eventos:
1. **Enviar candidatura** en T+2 días desde la detección.
2. **Follow-up** en T+14 días.

Formato ICS minimal, sin librería externa. Ver
[`backend/routers/watchlist.py`](../backend/routers/watchlist.py) → `_build_ics`.

### 4.6 Notificaciones dual-channel

- **Push inmediato**: si `score_final + urgency_score ≥ 70` y job de
  watchlist. Disparado tras `_save_results()` desde
  `_notify_watchlist_priority()`. Persiste como `Notification` + broadcast
  via Redis SSE.
- **Digest diario**: 18:00 CET, scores 40-69 acumulados en 24h. Tarea
  Celery `tasks.watchlist.send_digest`.

### 4.7 Healthcheck del módulo

Tarea Celery `tasks.watchlist.check_health` cada 6h. Detecta:
- Sources de watchlist con `is_allowed=False` (compliance kill-switch).
- `consecutive_blocks > 0`.
- `last_blocked_at` en las últimas 24h.

Notifica a los usuarios con `watchlist_schools_enabled=True`. Si Ecolint
rediseña su web y el scraper rompe → el usuario lo sabe en 6h.

Implementado en
[`backend/tasks/watchlist_tasks.py`](../backend/tasks/watchlist_tasks.py).

### 4.8 Schedule

Registrado en
[`backend/services/scheduler.py`](../backend/services/scheduler.py)
(APScheduler dispatcha a Celery):
- Scrapers watchlist: cada 6h (junto al resto de scrapers).
- Healthcheck: cada 6h.
- Digest: diario 18:00 CET.

---

## 5. Frontend

- **Badge en MatchCard**: distingue jobs de watchlist; badge "urg N" si
  urgency ≥ 30; badge de estado si !== "detected".
- **Pestaña "Watchlist" en MatchPage**: filtra solo jobs con `school_id`,
  ordenados por `score_final + urgency_score`.
- **Página /watchlist** (`WatchlistPage.jsx`): lista detallada con
  filtros por estado, botones para avanzar estado, generar borrador,
  descargar .ics, abrir oferta.
- **Toggle en ProfilePage**: "Vigilancia de colegios suizos" controla
  `watchlist_schools_enabled` del perfil.

---

## 6. Migraciones aplicadas

| Revisión | Descripción |
|----------|-------------|
| `b2c3d4e5f6a8` | Seed source_compliance para los 3 scrapers Fase 1 |
| `c3d4e5f6a8b9` | `user_profiles.watchlist_schools_enabled` (default true para usuarios existentes) |
| `d4e5f6a8b9c1` | `match_results.application_status`, `application_status_at`, `urgency_score`, `draft_letter` |
| `e5f6a8b9c1d2` | Seed source_compliance para los 5 nuevos scrapers Fase 2-3 |

---

## 7. Decisiones de diseño relevantes

### 7.1 Por qué NO un role classifier IT específico

El doc original incluía un MATCH/EXCLUDE/PARTIAL por keywords IT.
Decisión: descartar. El pipeline matching ya personaliza por usuario via
CV embedding + LLM reranking — añadir un clasificador por keywords
duplica responsabilidad y entra en conflicto con SRP. El usuario que
busque IT verá automáticamente buenos matches para "IT Manager" y bajos
para "IT teacher" gracias al embedding.

### 7.2 Por qué el bypass es per-user y no per-job

Versión inicial: el scraper forzaba `category="A"` en el dict del job.
Problema: jugaba mal con multi-user (cualquier otro usuario que matchee
contra esos jobs también recibiría el bypass aunque no esté en la
watchlist).

Versión final: el scraper deja la categoría real (probablemente `H`),
y `MatchService._category_multiplier_for(job, profile)` decide. Esto
permite que cada usuario active/desactive la watchlist independientemente.

### 7.3 Por qué descartar LinkedIn / jobs.ch / jobscout24

CLAUDE.md prohíbe expresamente scrapear estos portales. El doc original
los listaba como fuentes secundarias. Para Wisdom Tree (que publica solo
allí) implica categoría `manual` permanente.

### 7.4 Por qué descartar HTML snapshots 90 días

El doc original sugería retener snapshots HTML para diff legible. Coste
de storage alto para valor bajo: el dedup 3-nivel ya detecta cambios
reales. Si surge necesidad concreta se añade.

### 7.5 Por qué descartar TES RSS feed

El doc sugería RSS de TES como fuente principal para todos los colegios.
Tenemos `tes` scraper HTML que cubre lo mismo. Añadir RSS duplicaría
fuente sin ganancia neta — el dedup 3-nivel filtraría las duplicaciones
pero pagaríamos requests innecesarios.

### 7.6 Por qué no replicar el COBIS Global Jobs

COBIS es bolsa de empleo global para British International Schools.
El doc lo sugiere como complementario. Los 17 colegios de la watchlist
ya están cubiertos via sus portales propios. COBIS añadiría colegios
fuera de Suiza — fuera de scope geográfico.

---

## 8. Operaciones

### 8.1 Rate limits configurados

| Source | Rate limit (s entre req) | Max req/h |
|--------|--------------------------|-----------|
| swiss_schools_nae       | 2.0 | 60  |
| swiss_schools_isp       | 1.0 | 120 |
| swiss_schools_inspired  | 2.0 | 60  |
| swiss_schools_zis       | 3.0 | 60  |
| swiss_schools_isb       | 3.0 | 60  |
| swiss_schools_ecolint   | 3.0 | 60  |
| swiss_schools_hautlac   | 3.0 | 60  |
| swiss_schools_iscs      | 3.0 | 60  |

Todos con `auto_disable_on_block=true` y kill-switch tras 3 bloques
consecutivos (compliance default).

### 8.2 Volumen agregado a 2026-05-29

- 65 jobs activos en watchlist
- 11/17 colegios cubiertos automáticamente
- Top contribuyente: Ecolint (15) > NAE Champittet (14) > Mosaic (10)
  > Inspired St George's (8) > NAE La Côte (4) > ZIS (4) > NAE Beau
  Soleil (3) > Inspired GES (3) > Haut-Lac (2) > ISCS (2) > ISB (0)

### 8.3 Health check inicial

Ejecutado al implementar:
```
status: ok, checked: 8 sources, issues: 0
```

---

## 9. Archivos relevantes

### Backend
- [`backend/scrapers/swiss_schools_config.py`](../backend/scrapers/swiss_schools_config.py) — registro de los 17 colegios con metadata completa.
- [`backend/scrapers/swiss_schools_*.py`](../backend/scrapers/) — 8 scrapers individuales.
- [`backend/services/urgency_scorer.py`](../backend/services/urgency_scorer.py) — calcula urgency boost.
- [`backend/services/letter_generator.py`](../backend/services/letter_generator.py) — generador de cartas A/B con placeholders.
- [`backend/routers/watchlist.py`](../backend/routers/watchlist.py) — 5 endpoints API.
- [`backend/tasks/watchlist_tasks.py`](../backend/tasks/watchlist_tasks.py) — healthcheck + digest.
- [`backend/services/match_service.py`](../backend/services/match_service.py) — bypass per-user + push prioritario.
- [`backend/services/scheduler.py`](../backend/services/scheduler.py) — schedules registrados.

### Frontend
- [`frontend/src/pages/WatchlistPage.jsx`](../frontend/src/pages/WatchlistPage.jsx) — UI principal.
- [`frontend/src/components/MatchCard.jsx`](../frontend/src/components/MatchCard.jsx) — badges watchlist + urgency + estado.
- [`frontend/src/pages/MatchPage.jsx`](../frontend/src/pages/MatchPage.jsx) — pestaña Watchlist.
- [`frontend/src/pages/ProfilePage.jsx`](../frontend/src/pages/ProfilePage.jsx) — toggle activación.

### Migraciones
- `backend/alembic/versions/b2c3d4e5f6a8_seed_swiss_schools_compliance.py`
- `backend/alembic/versions/c3d4e5f6a8b9_add_watchlist_schools_enabled.py`
- `backend/alembic/versions/d4e5f6a8b9c1_match_result_state_machine.py`
- `backend/alembic/versions/e5f6a8b9c1d2_seed_swiss_schools_phase2.py`

---

## 10. Tareas pendientes / mejoras futuras

- [ ] Recibir y aplicar las plantillas A/B reales del usuario.
- [ ] Auto-transición `sent → followup_due` tras 14 días desde la
      transición a `sent` (tarea Celery diaria).
- [ ] Si surge interés: scrapers para los 6 colegios manuales
      (requeriría Playwright para 3 de ellos).
- [ ] Identificar el "colegio británico de Grindelwald" si existe
      realmente, o eliminar la referencia del doc original.
- [ ] Refinar parser ISCS: actualmente "PRIMARY and SECONDARY TEACHER"
      se trunca a "Primary" porque el HTML anida varios `<strong>`.
- [ ] Métricas operativas: dashboard simple con
      "% jobs watchlist por colegio en últimos 30 días" y "tiempo medio
      detected → sent".
