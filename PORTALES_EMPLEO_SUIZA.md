# Portales de Empleo en Suiza — Mas Alla de Tech

Documento generado: 2026-02-24
Objetivo: Identificar portales de empleo suizos **no exclusivamente tecnologicos** para ampliar la cobertura del dashboard.

**Ya integrados (excluidos):** Jobicy, Remotive, Arbeitnow, JSearch, RemoteOK, Himalayas, Adzuna, WeWorkRemotely, Ostjob.ch, Zentraljob.ch, SwissTechJobs.com, ICTjobs.ch

---

## BLOQUE A: Portales con API Gratuita (integracion directa)

### 1. Jooble (ch.jooble.org)
- **URL:** https://ch.jooble.org
- **Sectores:** Todos — agregador global, presencia fuerte en Suiza (#7 en ranking suizo)
- **API:** REST POST gratuita
  - **Endpoint:** `POST https://jooble.org/api/{api_key}`
  - **Documentacion:** https://jooble.org/api/about
  - **Autenticacion:** API key gratuita (registro)
  - **Request:** JSON con `keywords`, `location` (ciudades/cantones suizos), `radius`, `salary`, `page`
  - **Response:** JSON con `totalCount` + array de jobs (title, location, snippet, salary, source, type, link, company, updated, id)
  - **Limites:** Generosos para tier gratuito
- **Prioridad:** ALTA — integracion inmediata posible, patron identico a APIs existentes

### 2. Careerjet (careerjet.ch)
- **URL:** https://www.careerjet.ch
- **Sectores:** Todos — agregador global, 40M+ empleos de 70K+ sitios web (#15 en Suiza)
- **API:** REST GET gratuita
  - **Endpoint:** `GET https://search.api.careerjet.net/v4/query`
  - **Documentacion:** https://www.careerjet.com/partners/api
  - **Autenticacion:** API key gratuita (registro como partner)
  - **Params:** `keywords`, `location`, `contract_type`, `work_hours`, `sort`, `page_size`, locale `de_CH`
  - **Response:** JSON con listings (title, company, date, description, salary, URL)
  - **Cliente Python oficial:** `pip install careerjet-api` — https://github.com/careerjet/careerjet-api-client-python
- **Prioridad:** ALTA — integracion inmediata posible, tiene cliente Python oficial

### 3. Job-Room / arbeit.swiss (Servicio Publico Suizo — SECO)
- **URL:** https://www.job-room.ch / https://www.arbeit.swiss
- **Sectores:** TODOS — portal oficial de empleo publico suizo (RAV/ORP). Contiene todos los empleos sujetos a reporte obligatorio (>5% desempleo nacional) + publicaciones voluntarias. #2 en trafico en Suiza
- **API:** REST disponible, requiere credenciales
  - **Endpoint:** `https://api.job-room.ch/jobAdvertisements/v1`
  - **Documentacion:** https://test-api.job-room.ch/api-docs/jobAdvertisements/v1/index.html
  - **Autenticacion:** HTTP Basic Auth (credenciales solicitables a jobroom-api@seco.admin.ch)
  - **Operaciones:** POST (crear), GET /{id}, POST /_search
  - **Nota:** API documentada es employer-facing. Existe API de busqueda interna usada por el frontend. Contactar SECO para acceso
- **Prioridad:** MEDIA-ALTA — extremadamente valioso por ser #2 en Suiza y portal gubernamental. Requiere contactar SECO

### 4. Talent.com (ex-Neuvoo)
- **URL:** https://www.talent.com
- **Sectores:** Todos — agregador global, 30M+ empleos en 80 paises
- **API:** Programa de publishers con XML/API feed
  - **Documentacion:** https://www.talent.com/publishers (login requerido)
  - **Modelo:** Revenue share basado en CPC
- **Prioridad:** MEDIA — posible pero requiere acuerdo de partnership

---

## BLOQUE B: Portales que Requieren Scraping (sin API gratuita)

### Portales Generalistas Principales

| # | Portal | URL | Sectores | Notas | TOS Scraping |
|---|--------|-----|----------|-------|--------------|
| 5 | **JobCloud (jobs.ch / jobup.ch)** | jobs.ch / jobup.ch | Todos | #1 en Suiza (2.9M visitas/mes, 110K+ anuncios). API solo para partners comerciales (developer.jobcloud.io). XML feed employer-facing | **PROHIBIDO** — TOS explicitos contra scraping |
| 6 | **Indeed Switzerland** | ch.indeed.com | Todos | #4 en Suiza. API de busqueda DEPRECADA desde 2024. Sin alternativa gratuita | **PROHIBIDO** — TOS explicitos contra scraping |
| 7 | **Stellenanzeiger.ch** | stellenanzeiger.ch | Todos | Uno de los mas grandes de Suiza. Sin API ni RSS | Revisar robots.txt |
| 8 | **Jobagent.ch** | jobagent.ch | Todos | Meta-buscador suizo. Sin API | Revisar robots.txt |
| 9 | **Monster Switzerland** | monster.ch | Todos | #6 en Suiza. Sin API publica | Revisar robots.txt |
| 10 | **Jobchannel.ch** | jobchannel.ch | Todos | Red de 200+ portales especializados, 4M vistas/mes. Sin API | Revisar robots.txt |
| 11 | **Workpool-jobs.ch** | workpool-jobs.ch | Todos | Agregador con 70K+ anuncios (solo aleman). Sin API | Revisar robots.txt |

### Portales del Sector Publico / Gobierno

| # | Portal | URL | Sectores | Notas |
|---|--------|-----|----------|-------|
| 12 | **stelle.admin.ch** | stelle.admin.ch | Administracion federal suiza | Empleos del gobierno federal. Contenido renderizado con JS, requiere headless browser |
| 13 | **EURES** | eures.europa.eu | Todos (31 paises europeos) | Portal europeo de movilidad laboral. ~3M empleos. Sin API de busqueda publica |

### Portales de Salud / Sanidad

| # | Portal | URL | Sectores | Notas |
|---|--------|-----|----------|-------|
| 14 | **med-jobs.ch** | med-jobs.com | Sanidad — medicos, enfermeros, asistentes, matronas | Alcanza 140K+ medicos en CH/DE/FR/AT. Sin API |
| 15 | **Health Hero Jobs** | healthherojobs.com | Sanidad — hospitales y centros medicos | Basado en Bussigny, VD. Sin API |
| 16 | **Sozjobs.ch** | sozjobs.ch | Trabajo social y sanidad | Sin API |

### Portales de Hosteleria / Gastronomia

| # | Portal | URL | Sectores | Notas |
|---|--------|-----|----------|-------|
| 17 | **Hotelcareer / Hoteljob.ch** | hotelcareer.com / hoteljob.ch | Hosteleria, turismo, restauracion | Grupo StepStone. XML feed solo employer-facing |
| 18 | **Gastrojob.ch** | gastrojob.ch | Hoteles, restaurantes, turismo, panaderia | Hotel & Gastro Union. Sin API |
| 19 | **HOGASTJOB.com** | hogastjob.com | Hosteleria y gastronomia DACH | Sin API |

### Portales de Finanzas

| # | Portal | URL | Sectores | Notas |
|---|--------|-----|----------|-------|
| 20 | **Financejobs.ch** | financejobs.ch | Finanzas, banca | Sin API |
| 21 | **BankingJobs.ch** | bankingjobs.ch | Banca, funciones corporativas | Sin API |

### Portales de Educacion / Ciencia

| # | Portal | URL | Sectores | Notas |
|---|--------|-----|----------|-------|
| 22 | **job.educa.ch** | job.educa.ch | Educacion — docencia a todos los niveles | Proyecto conjunto SERI/EDK. Sin API |
| 23 | **myScience.ch** | myscience.ch/jobs | Ciencia, investigacion, academia, ingenieria | Universidades y centros de investigacion. Sin API |
| 24 | **cinfo.ch** | cinfo.ch/en/jobs | Cooperacion internacional, ayuda humanitaria | Mandato SDC/SECO. Sin API |

### Portales Regionales

| # | Portal | URL | Sectores | Notas |
|---|--------|-----|----------|-------|
| 25 | **Berner-Stellen.ch** | berner-stellen.ch | Todos (Canton Berna) | Sin API |

### Portales para Angloparlantes / Expats

| # | Portal | URL | Sectores | Notas |
|---|--------|-----|----------|-------|
| 26 | **EnglishJobSearch.ch** | englishjobsearch.ch | Todos (posiciones en ingles) | Sin API |
| 27 | **TheLocal.ch/jobs** | thelocal.ch/jobs | Todos (ingles — docencia, traduccion, ingenieria) | Sin API |
| 28 | **JobsInZurich.com** | jobsinzurich.com | Todos (ingles, enfoque Zurich) | Sin API |

### Redes Profesionales / Plataformas Internacionales

| # | Portal | URL | Sectores | Notas | TOS Scraping |
|---|--------|-----|----------|-------|--------------|
| 29 | **LinkedIn** | linkedin.com/jobs | Todos | API solo para partners (requiere LinkedIn Relationship Manager) | **PROHIBIDO** — contra TOS, riesgo legal alto |
| 30 | **Glassdoor** | glassdoor.ch | Todos + reviews | API de busqueda solo partner. API de company limitada | **PROHIBIDO** — contra TOS |
| 31 | **XING** | xing.com | Todos (DACH) | Red profesional germanofona. Sin API publica | **PROHIBIDO** — contra TOS |

### Agencias de Empleo (sin APIs)

| # | Portal | URL | Sectores | Ranking Suiza |
|---|--------|-----|----------|---------------|
| 32 | **Adecco** | adecco.com/en-ch | Todos | #3 |
| 33 | **Manpower** | manpower.ch | Todos | #5 |
| 34 | **Randstad** | randstad.ch | Todos | #9 |

### Aprendizajes

| # | Portal | URL | Sectores | Notas |
|---|--------|-----|----------|-------|
| 35 | **Yousty.ch** | yousty.ch | Aprendizajes (Lehrstellen) | Mayor bolsa de aprendizajes de Suiza. API bajo demanda (partnership) |

---

## Resumen: Prioridad de Integracion

| Prioridad | Portal | Tipo API | Esfuerzo | Valor | TOS OK |
|-----------|--------|----------|----------|-------|--------|
| **1** | **Jooble** | REST POST gratuita | Bajo (patron identico) | Alto — todos los sectores | Si |
| **2** | **Careerjet** | REST GET gratuita | Bajo (cliente Python) | Alto — 40M+ empleos | Si |
| **3** | **Job-Room (SECO)** | REST (contactar SECO) | Medio (credenciales) | Muy alto — #2 Suiza, gubernamental | Si |
| **4** | **Talent.com** | Publisher API | Medio (partnership) | Alto — 30M+ empleos | Si |
| **5** | **stelle.admin.ch** | Scraping (headless) | Alto | Medio — empleos federales | Si (publico) |
| **6** | **med-jobs.ch** | Scraping | Medio | Medio-alto — nicho salud | Verificar |
| **7** | **Gastrojob.ch** | Scraping | Medio | Medio — nicho hosteleria | Verificar |
| **X** | ~~jobs.ch / Indeed / LinkedIn~~ | ~~Scraping~~ | ~~—~~ | ~~—~~ | **NO — TOS prohiben scraping** |

---

## Fuentes
- [Jooble API](https://jooble.org/api/about) | [Docs](https://help.jooble.org/en/support/solutions/articles/60001448238)
- [Careerjet API](https://www.careerjet.com/partners/api) | [Python Client](https://github.com/careerjet/careerjet-api-client-python)
- [Job-Room API Docs](https://test-api.job-room.ch/api-docs/jobAdvertisements/v1/index.html)
- [JobCloud Developer](https://developer.jobcloud.io/) | [XML Specs](https://www.jobcloud.ch/c/en/technical-solution/xml-fields/)
- [Swiss Job Ranking](https://www.jobrank.org/job-sites/ch/)
- [Best Swiss Job Sites — JOIN](https://join.com/recruitment-hr-blog/swiss-job-sites)
- [Swiss Job Search — Comparis](https://en.comparis.ch/neu-in-der-schweiz/arbeit/jobsuche)

---
---

# PLAN UNIFICADO: SwissJobHunter — Webapp Autonoma de Busqueda de Empleo con IA

## 1. Vision General

**Nombre:** SwissJobHunter
**Objetivo:** Webapp standalone que automatiza la busqueda de empleo en Suiza, agregando multiples portales (APIs + scraping) y aplicando IA para matching inteligente, adaptacion de CV, alertas personalizadas y gestion del pipeline de candidaturas.

**Diferencia con ReactPortfolio Dashboard:** El dashboard actual es una herramienta de monitorizacion para un solo usuario (admin). SwissJobHunter sera un producto multi-usuario con busqueda activa automatizada, perfiles de candidato configurables, alertas en tiempo real y un pipeline de candidatura completo.

### 1.1 Las 3 Features que Definen el Exito

| # | Feature | Por que |
|---|---------|---------|
| 1 | **Pipeline IA (matching + explicacion + CV adapter)** | Diferenciador unico frente a LinkedIn/Indeed/jobs.ch |
| 2 | **Volumen de datos (Jooble + Careerjet + SECO + scrapers)** | Sin datos de calidad, la IA es inutil |
| 3 | **UX movil nativa (Capacitor + PWA + swipe + push + onboarding)** | Garantiza retencion y uso diario — app nativa real en App Store/Play Store, push sin limitaciones iOS, offline robusto |

---

## 2. Arquitectura del Sistema

**Enfoque: Mobile-first con Capacitor (app nativa) + PWA fallback + Celery workers**

**Estrategia de distribucion:** Una unica base de codigo React que se despliega en 3 targets:
- **Web (PWA):** Para desktop y como fallback mobile via browser
- **iOS (Capacitor → App Store):** App nativa real con WKWebView + plugins nativos
- **Android (Capacitor → Play Store):** App nativa real con WebView + plugins nativos

**Por que Capacitor y no React Native/Flutter:**
- **95% de reutilizacion** del codigo React existente (mismo JSX, TailwindCSS, hooks, Zustand)
- React Native requeriria reescribir toda la UI (~60% del frontend) — `<View>` en vez de `<div>`, sin CSS
- Flutter requeriria reescribir TODO en Dart — 0% reutilizacion
- Capacitor envuelve la web app en un shell nativo con acceso a APIs nativas via plugins

```
┌─────────────────────────────────────────────────────────────────┐
│               CAPACITOR NATIVE SHELL (iOS + Android)             │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                 FRONTEND (SPA — misma base de codigo)      │  │
│  │  React 19 + Vite + TailwindCSS + shadcn/ui               │  │
│  │                                                            │  │
│  │  DESKTOP (web): Sidebar fija + contenido principal         │  │
│  │  ┌─────┬───────────────────────────────────────┐          │  │
│  │  │ NAV │         CONTENIDO PRINCIPAL           │          │  │
│  │  │  🔍 │  ┌──────────┐ ┌──────────┐            │          │  │
│  │  │  🤖 │  │ Job Card │ │ Job Card │            │          │  │
│  │  │  📋 │  └──────────┘ └──────────┘            │          │  │
│  │  │  👤 │  ┌──────────┐ ┌──────────┐            │          │  │
│  │  │  ⚙️ │  │ Job Card │ │ Job Card │            │          │  │
│  │  └─────┴───────────────────────────────────────┘          │  │
│  │                                                            │  │
│  │  MOBILE (Capacitor): Contenido full-width + bottom tab     │  │
│  │  ┌─────────────────────────┐                              │  │
│  │  │  CONTENIDO 100vw        │  ← Pull-to-refresh          │  │
│  │  │  ┌───────────────────┐  │  ← Swipe entre cards        │  │
│  │  │  │   Job Card        │  │  ← Touch targets 44px+      │  │
│  │  │  └───────────────────┘  │                              │  │
│  │  ├─────────────────────────┤                              │  │
│  │  │ 🔍  🤖  📋  👤  ⚙️     │  ← Bottom tab bar           │  │
│  │  └─────────────────────────┘                              │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  CAPACITOR PLUGINS (acceso nativo):                              │
│  📲 Push Notifications (nativas, sin limitaciones iOS)           │
│  📷 Camera (CV upload nativo)                                    │
│  💾 SQLite (offline robusto, sin limite 50MB)                    │
│  🔄 Background Fetch (sync periodico)                            │
│  🔐 Biometric Auth (Face ID / fingerprint)                      │
│  📳 Haptics (feedback tactil nativo)                             │
│  📤 Share Sheet (compartir ofertas)                               │
│  🌐 Service Worker (PWA fallback para web)                       │
└───────────────────────┬─────────────────────────────────────────┘
                        │ REST API + SSE + Push (nativo)
┌───────────────────────┴─────────────────────────────────────────┐
│                      BACKEND (FastAPI)                           │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
│  │  Auth Service    │  │  Job Aggregator  │  │  AI Matcher    │  │
│  │  (JWT + OAuth2)  │  │  (Strategy Pat.) │  │  (2 etapas)    │  │
│  └─────────────────┘  └─────────────────┘  └────────────────┘  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
│  │  Notification    │  │  CV Adapter     │  │  CV Parser     │  │
│  │  (SSE+Email+Push)│  │  (LLM-powered)  │  │  (PDF/DOCX)    │  │
│  └─────────────────┘  └─────────────────┘  └────────────────┘  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
│  │  Scraper Engine  │  │  Compliance     │  │  Data Quality  │  │
│  │  (Playwright)    │  │  Engine (TOS)   │  │  (Norm + Dedup)│  │
│  └─────────────────┘  └─────────────────┘  └────────────────┘  │
└───────────────────────┬─────────────────────────────────────────┘
                        │
┌───────────────────────┴─────────────────────────────────────────┐
│                   CELERY WORKERS (tareas pesadas)                │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
│  │  Embedding Batch │  │  Scraper Jobs   │  │  CV Adaptation │  │
│  │  (vectorizar)    │  │  (Playwright)   │  │  (Groq LLM)   │  │
│  └─────────────────┘  └─────────────────┘  └────────────────┘  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
│  │  AI Matching     │  │  Email Digests  │  │  Health Checks │  │
│  │  (incremental)   │  │  (daily/weekly) │  │  (URL validity)│  │
│  └─────────────────┘  └─────────────────┘  └────────────────┘  │
└───────────────────────┬─────────────────────────────────────────┘
                        │
┌───────────────────────┴─────────────────────────────────────────┐
│                     INFRAESTRUCTURA                              │
│  PostgreSQL 16 (pgvector) │ Redis 7 (broker + cache) │ MinIO   │
│  APScheduler (dispatcher) │ Prometheus + Grafana │ Loki (logs) │
└─────────────────────────────────────────────────────────────────┘
```

**Cambios clave vs plan original:**
- **Capacitor** como shell nativo para iOS/Android — misma base de codigo React, pero distribuida como app nativa real en App Store y Play Store. PWA se mantiene como fallback para acceso web/desktop.
- **Celery + Redis** como broker desde el dia 1 — APScheduler solo despacha tareas a Celery, no ejecuta trabajo pesado inline. Evita bloquear el event loop de FastAPI.

---

## 3. Stack Tecnologico

| Capa | Tecnologia | Justificacion |
|------|-----------|---------------|
| **Frontend** | React 19 + Vite 7 | Mismo stack que ReactPortfolio — ~95% reutilizable con Capacitor |
| **Native Shell** | **Capacitor 6** | Envuelve la SPA React en app nativa real (iOS WKWebView + Android WebView) con plugins nativos. Una base de codigo → 3 targets (web, iOS, Android) |
| **UI Kit** | TailwindCSS + shadcn/ui | Mobile-first, componentes accesibles, temas, responsive |
| **State** | Zustand | Mas ligero que Redux, perfecto para multi-store |
| **PWA (web fallback)** | vite-plugin-pwa + Workbox | Fallback para acceso desktop/browser. En mobile, Capacitor reemplaza el Service Worker |
| **Offline (mobile)** | @capacitor/preferences + @capacitor-community/sqlite | SQLite nativo para offline robusto (sin limite 50MB de PWA). Preferences para key-value ligero |
| **Push (mobile)** | @capacitor/push-notifications | Push nativas reales via APNs (iOS) y FCM (Android) — sin limitaciones de Web Push en iOS |
| **Camera** | @capacitor/camera | Upload de CV desde camara/galeria con UX nativa |
| **Biometrics** | capacitor-native-biometric | Face ID / fingerprint para login rapido |
| **Haptics** | @capacitor/haptics | Feedback tactil nativo en swipe/acciones |
| **Mobile UX** | @dnd-kit/sortable + Framer Motion | Drag & drop touch-native, swipe gestures, 60fps |
| **Routing** | react-router-dom 7 | Lazy loading, bottom tab bar en mobile |
| **Backend** | FastAPI 0.115+ | Async nativo, OpenAPI auto, probado en ReactPortfolio |
| **Workers** | Celery 5 + Redis broker | Tareas pesadas fuera del event loop (embedding, scraping, LLM) |
| **Scheduler** | APScheduler → Celery dispatch | Cron triggers despachan a workers, no ejecutan inline |
| **ORM** | SQLAlchemy 2.0 async | Migraciones con Alembic |
| **DB** | PostgreSQL 16 + pgvector | JSONB, full-text search, vector similarity nativo |
| **Cache** | Redis 7 | Cache de jobs, rate limiting, pub/sub para SSE, Celery broker |
| **AI Embeddings** | paraphrase-multilingual-MiniLM-L12-v2 | **Multilingue** (DE/FR/EN/IT), 384 dims, crucial para mercado suizo |
| **AI LLM** | Groq (Llama 3.1 70B) | Re-ranking, explicaciones, CV adaptation. Tier gratuito generoso |
| **Scraping** | Playwright (async) | Headless browser para portales sin API |
| **CV Parsing** | python-docx + PyMuPDF | Extraccion de skills desde PDF/DOCX |
| **Email** | FastAPI-Mail + Jinja2 templates | Alertas y digests |
| **Auth** | JWT + OAuth2 (Google/GitHub) | Patron probado en ReactPortfolio + biometric login via Capacitor |
| **Observabilidad** | Prometheus + Grafana + Loki | Metricas por provider, alerting, structured logs |
| **Deploy (backend)** | Docker Compose → VPS / Railway | Inicio simple, escalar despues |
| **Deploy (mobile)** | Capacitor → Xcode (iOS) + Android Studio | Builds nativos para App Store y Play Store |

**Cambios clave vs plan original:**
- **Capacitor 6** como shell nativo — misma SPA React desplegada como app nativa en iOS/Android. Elimina limitaciones de PWA en iOS (push reales, offline ilimitado, background fetch, biometrics).
- **`paraphrase-multilingual-MiniLM-L12-v2`** en vez de `all-MiniLM-L6-v2` — Suiza tiene ofertas en 4 idiomas, un modelo solo-ingles pierde matches cross-language.
- **PWA se mantiene** como fallback para acceso web/desktop, pero la experiencia mobile principal es la app nativa via Capacitor.

---

## 4. Compliance, Legal y Seguridad

> **Esta seccion es OBLIGATORIA, no opcional.** El sistema almacena CVs, datos personales y historial de busqueda. Suiza tiene la nDSG (nueva Ley Federal de Proteccion de Datos) vigente desde sept. 2023.

### 4.1 GDPR / nDSG

| Requisito | Implementacion |
|-----------|---------------|
| **Consentimiento explicito** | Checkbox obligatorio antes de procesar CV con IA. Registro del consentimiento con timestamp |
| **Derecho al olvido** | Endpoint `DELETE /api/v1/profile/delete-all` que elimina perfil, CVs, embeddings, matches, applications |
| **Politica de retencion** | Cuentas inactivas >12 meses: notificar → 30 dias → anonimizar datos |
| **Portabilidad** | Endpoint `GET /api/v1/profile/export` que exporta todos los datos del usuario en JSON |
| **Transparencia IA** | Explicar al usuario como funciona el scoring, que datos se usan, que modelo procesa su CV |
| **Minimizacion** | Solo recopilar datos necesarios. CV text se procesa → embedding → texto original eliminable |

### 4.2 Compliance Engine (TOS por fuente)

Tabla `source_compliance` en la DB:

```python
class SourceCompliance(Base):
    __tablename__ = "source_compliance"

    source_key: str          # "jooble", "careerjet", "stelle_admin", etc.
    method: str              # "api" | "scraping"
    is_allowed: bool         # Kill-switch: False = desactivar inmediatamente
    rate_limit_seconds: float # Delay minimo entre requests
    robots_txt_ok: bool      # Resultado de verificar robots.txt
    tos_reviewed_at: date    # Fecha de ultima revision de TOS
    tos_notes: str           # Notas sobre restricciones especificas
    max_requests_per_hour: int
    last_blocked_at: datetime | None  # Si detectamos bloqueo
    auto_disable_on_block: bool       # Desactivar automaticamente si bloqueado
```

**Reglas:**
- Antes de cada scraping run, verificar `is_allowed = True`
- Si un scraper recibe 403/429 tres veces seguidas → `auto_disable_on_block` activa kill-switch
- Revision trimestral de TOS de cada fuente
- **NUNCA scrapear** jobs.ch, Indeed, LinkedIn, Glassdoor, XING (TOS explicitos)

### 4.3 Seguridad

| Medida | Implementacion |
|--------|---------------|
| **Rate limiting por usuario/IP** | slowapi: 100 req/min general, 10 req/min para AI endpoints |
| **CAPTCHA en auth** | hCaptcha en registro y login tras 3 intentos fallidos |
| **Cifrado de CVs at-rest** | MinIO con server-side encryption (SSE-S3) |
| **Rotacion de secrets** | JWT secret rotation cada 90 dias, API keys en vault |
| **Auditoria de accesos** | Log de accesos a datos sensibles (CV, perfil) con user_id + timestamp |
| **Input sanitization** | Pydantic v2 strict mode + bleach para HTML en descripciones scrapeadas |
| **CORS restrictivo** | Solo dominios propios en produccion |

---

## 5. Modelo de Datos

### 5.1 Entidades Principales

```
┌──────────────┐     ┌───────────────────┐     ┌──────────────────┐
│    User      │     │   UserProfile     │     │   SavedSearch    │
│──────────────│     │───────────────────│     │──────────────────│
│ id (UUID)    │──┐  │ id (UUID)         │     │ id (UUID)        │
│ email        │  │  │ user_id (FK)      │     │ user_id (FK)     │
│ hashed_pass  │  ├──│ title (desired)   │     │ name             │
│ is_active    │  │  │ skills (JSONB)    │     │ filters (JSONB)  │
│ created_at   │  │  │ experience_years  │     │ min_score (int)  │
│ plan (enum)  │  │  │ languages (JSONB) │     │ notify_email     │
│ last_login   │  │  │ locations (JSONB) │     │ notify_push      │
│ gdpr_consent │  │  │ salary_min        │     │ notify_frequency │
│ gdpr_date    │  │  │ salary_max        │     │ last_notified_at │
└──────────────┘  │  │ remote_pref       │     │ last_run_at      │
                  │  │ cv_text (Text)    │     │ total_matches    │
                  │  │ cv_embedding (Vec)│     │ is_active        │
                  │  │ score_weights JSON│     │ created_at       │
                  │  │ updated_at        │     └──────────────────┘
                  │  └───────────────────┘
                  │
                  │  ┌───────────────────┐     ┌──────────────────┐
                  │  │  JobApplication   │     │   Job (cached)   │
                  │  │───────────────────│     │──────────────────│
                  ├──│ id (UUID)         │     │ hash (PK, str)   │
                  │  │ user_id (FK)      │     │ source (str)     │
                  │  │ job_hash (FK)     │     │ title            │
                  │  │ status (enum)     │     │ company          │
                  │  │ ai_score (int)    │     │ location         │
                  │  │ notes (Text)      │     │ canton (str)     │
                  │  │ follow_up_date    │     │ description      │
                  │  │ applied_at        │     │ url              │
                  │  │ adapted_cv_id(FK) │     │ salary_min_chf   │
                  │  │ created_at        │     │ salary_max_chf   │
                  │  │ updated_at        │     │ salary_original  │
                  │  └───────────────────┘     │ salary_currency  │
                  │                            │ salary_period    │
                  │  ┌───────────────────┐     │ remote (bool)    │
                  │  │ AIMatchResult     │     │ language (str)   │
                  │  │───────────────────│     │ seniority (str)  │
                  ├──│ id (UUID)         │     │ contract_type    │
                  │  │ user_id (FK)      │     │ tags (JSONB)     │
                  │  │ job_hash (FK)     │     │ embedding (Vec)  │
                  │  │ score_embedding   │     │ first_seen_at    │
                  │  │ score_llm         │     │ last_seen_at     │
                  │  │ score_final       │     │ is_active (bool) │
                  │  │ explanation (Text)│     │ url_last_check   │
                  │  │ matching_skills   │     └──────────────────┘
                  │  │ missing_skills    │
                  │  │ feedback (enum)   │     ┌──────────────────┐
                  │  │ feedback_implicit │     │ SourceCompliance │
                  │  │ created_at        │     │──────────────────│
                  │  └───────────────────┘     │ source_key (PK)  │
                  │                            │ method           │
                  │  ┌───────────────────┐     │ is_allowed       │
                  │  │   AdaptedCV       │     │ rate_limit_secs  │
                  └──│───────────────────│     │ robots_txt_ok    │
                     │ id (UUID)         │     │ tos_reviewed_at  │
                     │ user_id (FK)      │     │ max_req_per_hour │
                     │ job_hash (FK)     │     │ auto_disable     │
                     │ match_id (FK)     │     └──────────────────┘
                     │ language (str)    │
                     │ version (int)     │
                     │ content (JSON)    │
                     │ created_at        │
                     └───────────────────┘
```

### 5.2 Enums Clave

```python
class ApplicationStatus(str, Enum):
    saved = "saved"
    applied = "applied"
    phone_screen = "phone_screen"
    technical = "technical"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"

class RemotePreference(str, Enum):
    remote_only = "remote_only"
    hybrid = "hybrid"
    onsite = "onsite"
    any = "any"

class NotifyFrequency(str, Enum):
    realtime = "realtime"     # SSE push inmediato
    daily = "daily"           # Digest diario por email
    weekly = "weekly"         # Digest semanal

class MatchFeedback(str, Enum):
    thumbs_up = "thumbs_up"
    thumbs_down = "thumbs_down"
    applied = "applied"       # Feedback implicito fuerte

class SalaryPeriod(str, Enum):
    yearly = "yearly"
    monthly = "monthly"
    hourly = "hourly"

class Seniority(str, Enum):
    intern = "intern"
    junior = "junior"
    mid = "mid"
    senior = "senior"
    lead = "lead"
    head = "head"
    director = "director"

class ContractType(str, Enum):
    full_time = "full_time"
    part_time = "part_time"
    contract = "contract"
    internship = "internship"
    apprenticeship = "apprenticeship"
    temporary = "temporary"
```

**Campos nuevos vs plan original:** `canton`, `salary_min_chf` (normalizado), `salary_period`, `language` (idioma de la oferta), `seniority`, `contract_type`, `url_last_check`, `feedback_implicit`, `version` (en AdaptedCV), `gdpr_consent`/`gdpr_date` (en User), `score_weights` (en UserProfile).

---

## 6. Calidad de Datos: Normalizacion y Deduplicacion

> **Gap critico del plan original.** Sin normalizacion y dedup robusta, el usuario ve la misma oferta repetida de multiples fuentes con datos inconsistentes. Esto destruye la confianza en el producto.

### 6.1 Deduplicacion Robusta (3 niveles)

El plan original usaba solo `MD5(title+company)` — insuficiente porque "Senior Python Dev" y "Senior Python Developer" no matchean.

```python
# Nivel 1: Exacta (rapido, en cada ingestion)
def dedup_exact(job):
    """URL canonica + source_id. Detecta reingestiones del mismo portal."""
    return hash(normalize_url(job.url)) or hash(f"{job.source}:{job.source_id}")

# Nivel 2: Fuzzy-lite (rapido, en cada ingestion)
def dedup_fuzzy(job):
    """Titulo + empresa normalizados. Detecta misma oferta cross-source."""
    title_norm = normalize(job.title)    # lowercase, strip "Senior/Jr/m-f-d", strip punctuation
    company_norm = normalize(job.company) # lowercase, strip "AG/GmbH/SA/Ltd"
    return md5(f"{title_norm}|{company_norm}")

# Nivel 3: Semantica (batch diario, en Celery worker)
async def dedup_semantic(new_jobs, existing_embeddings, threshold=0.95):
    """Embeddings con cosine > 0.95 = probable duplicado."""
    new_embeddings = model.encode([build_text(j) for j in new_jobs])
    similarities = cosine_similarity(new_embeddings, existing_embeddings)
    for i, job in enumerate(new_jobs):
        max_sim = similarities[i].max()
        if max_sim > threshold:
            job.duplicate_of = existing_jobs[similarities[i].argmax()].hash
```

### 6.2 Normalizacion de Campos

| Campo | Problema | Solucion |
|-------|----------|----------|
| **Salario** | Viene en CHF/EUR/USD, anual/mensual/horario | Normalizar todo a `salary_min_chf` y `salary_max_chf` anuales. Guardar originales en `salary_original`, `salary_currency`, `salary_period`. Conversion EUR→CHF con rate diario |
| **Ubicacion** | "Zurich", "Zürich", "8001 Zurich", "ZH" | Mapear a `canton` (enum de 26 cantones) + `city` normalizada. Geocoding con coordenadas para filtro por radio |
| **Idioma oferta** | No viene en la API — oferta puede ser en DE/FR/EN/IT | Deteccion automatica con `langdetect` o `fasttext`. Campo `language` en Job |
| **Seniority** | "Senior", "Sr.", "Lead", "Junior" mezclados en titulo | Extraer de titulo con regex → enum `Seniority`. Remover del titulo normalizado |
| **Tipo contrato** | "Full-time", "Vollzeit", "100%", "Festanstellung" | Mapear a enum `ContractType`. Detectar % para part-time |
| **Skills/tags** | Inconsistentes entre fuentes | Normalizar a lowercase, dedup ("React.js" = "reactjs" = "React"), limitar a 15 por job |

```python
# services/data_quality.py
class DataNormalizer:
    """Normaliza campos de jobs a formato consistente."""

    CANTON_MAP = {
        "zurich": "ZH", "zürich": "ZH", "zh": "ZH",
        "bern": "BE", "berne": "BE", "be": "BE",
        "geneve": "GE", "geneva": "GE", "genf": "GE",
        # ... 26 cantones con variantes DE/FR/IT/EN
    }

    async def normalize_job(self, raw_job: dict) -> dict:
        job = {**raw_job}
        job["salary_min_chf"] = self._normalize_salary(job, "min")
        job["salary_max_chf"] = self._normalize_salary(job, "max")
        job["canton"] = self._normalize_location(job.get("location", ""))
        job["language"] = self._detect_language(job.get("description", ""))
        job["seniority"] = self._extract_seniority(job.get("title", ""))
        job["contract_type"] = self._normalize_contract(job)
        job["tags"] = self._normalize_tags(job.get("tags", []))
        return job
```

### 6.3 Health Checks de Ofertas

```python
# Celery task: semanal
@celery_app.task
async def check_job_urls():
    """Verificar que las URLs de ofertas siguen activas."""
    active_jobs = await get_active_jobs_older_than(days=3)
    for job in active_jobs:
        status = await head_request(job.url, timeout=10)
        if status in (404, 410, None):
            job.is_active = False
            job.url_last_check = now()
        elif status == 200:
            job.url_last_check = now()
            job.last_seen_at = now()
```

---

## 7. Pipeline de IA (2 Etapas)

### 7.1 Etapa 1: Embedding Matching (rapido, masivo)

```
UserProfile.cv_embedding ←→ Job.embedding
         ↓
   cosine_similarity() via pgvector
         ↓
   Top N candidatos (N=100-500)
```

**Modelo: `paraphrase-multilingual-MiniLM-L12-v2`**
- 384 dimensiones, compatible con pgvector
- Soporta DE/FR/EN/IT — crucial para Suiza
- ~2x mas lento que MiniLM-L6 pero ~30% mejor en cross-language
- CPU-only en VPS: ~0.5s por batch de 100 jobs (aceptable)

**Mejoras sobre ReactPortfolio:**
- **Embeddings persistidos en DB** con pgvector (no recalcular)
- **Perfil vectorizado** desde CV completo + skills + titulo deseado
- **Actualizacion incremental**: solo nuevos jobs se vectorizan (hash-based)
- **Batch processing** en Celery worker (no bloquea FastAPI)

### 7.2 Etapa 2: LLM Re-ranking (preciso, selectivo)

```
Top N de Etapa 1
      ↓
  Groq Llama 3.1 70B
  Prompt: "Dado este perfil y este job, puntua 0-100 y explica"
      ↓
  score_final = weighted_sum(embedding, llm, salary, location, recency)
      ↓
  Top K resultados con explicaciones
```

**Ranking explicable** — el usuario ve POR QUE sube o baja cada oferta:

```json
{
  "score_final": 82,
  "breakdown": {
    "skills_match": 90,
    "salary_fit": 75,
    "location_fit": 85,
    "language_fit": 70,
    "recency": 95
  },
  "matching_skills": ["Python", "FastAPI", "PostgreSQL"],
  "missing_skills": ["Kubernetes", "Go"],
  "transferable_skills": [
    {"skill": "Docker", "applies_to": "Kubernetes", "explanation": "Container orchestration experience"}
  ],
  "reason": "Strong backend match. Missing K8s but Docker experience is transferable."
}
```

### 7.3 Scoring Multi-factor Configurable

```python
score_final = (
    w_embedding * score_embedding +      # Similitud semantica (default 0.25)
    w_llm * score_llm +                  # Evaluacion contextual (default 0.35)
    w_salary * score_salary_match +       # Rango salarial (default 0.15)
    w_location * score_location_match +   # Preferencia ubicacion (default 0.15)
    w_recency * score_recency             # Frescura del anuncio (default 0.10)
)
# Pesos ajustables por usuario via sliders touch-friendly en UI
```

### 7.4 Feedback Loop Inteligente

**Explicito:**
- Thumbs up / thumbs down en cards de match

**Implicito (signals del comportamiento):**

| Accion | Tipo signal | Peso |
|--------|------------|------|
| Abrir detalle de oferta | Positiva debil | +0.1 |
| Tiempo lectura > 10s en detalle | Positiva media | +0.2 |
| Guardar oferta / anadir a pipeline | Positiva fuerte | +0.5 |
| Aplicar (generar CV adaptado o click "Apply") | Positiva muy fuerte | +1.0 |
| Scroll rapido sin abrir (< 2s visible) | Negativa debil | -0.1 |
| Dismiss / swipe left | Negativa media | -0.3 |

**Ajuste gradual:** Las signals acumuladas modifican `score_weights` del `UserProfile` con moving average exponencial. Re-entrenamiento semanal en Celery worker.

### 7.5 Cache de Re-ranking

- Mismo job + mismo perfil (hash de cv_text + skills) → no re-evaluar por 7 dias
- **Invalidar cache** si el usuario actualiza su CV o skills
- Cache por hash del prompt completo, no solo por IDs

---

## 8. CV Adapter + Cover Letter (Feature Estrella)

### 8.1 Objetivo

Cuando el usuario encuentra un match interesante, la IA genera:
1. **CV adaptado**: version personalizada optimizada para esa oferta
2. **Cover letter completa**: carta de presentacion (no solo bullet points)

### 8.2 Arquitectura

```
┌──────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────────┐
│ Usuario  │    │  Frontend    │    │   Backend   │    │   Groq LLM   │
│ tap      │───→│ POST /adapt  │───→│ CV Adapter  │───→│ Llama 3.1    │
│ "Adapt"  │    │ {job, lang}  │    │ Service     │    │ 70B Versatile│
└──────────┘    └──────────────┘    └──────┬──────┘    └──────────────┘
                                           │
                                    ┌──────┴──────┐
                                    │ UserProfile │
                                    │ .cv_text    │
                                    │ .skills     │
                                    │ .experience │
                                    └─────────────┘
```

### 8.3 Servicio Backend

```python
# services/cv_adapter.py
CV_ADAPT_SYSTEM_PROMPT = """You are an expert career consultant for the Swiss job market.

Rules:
1. NEVER fabricate experience or skills the candidate doesn't have
2. Reorder sections to highlight the most relevant experience first
3. Rewrite the Profile/Summary to align with job requirements
4. For matching skills: emphasize prominently
5. For transferable skills: explain how existing experience applies
6. For missing skills: mention willingness to learn (only if related skills exist)
7. Keep factual content — only change emphasis, ordering, and framing
8. Output language MUST match the requested language (de/fr/en)

Output: JSON with sections (profile, experience[], skills[], education[], languages[])
Additionally: a complete cover_letter field (3-4 paragraphs, professional tone)"""

class CVAdapter:
    async def adapt_cv(self, user_profile, job, language="en", match_result=None) -> dict:
        """Generate tailored CV + cover letter for a specific job."""
        # Ejecutado en Celery worker (no bloquea FastAPI)
        # Returns: adapted_profile, adapted_experience, highlighted_skills,
        #          transferable_skills, suggested_additions, cover_letter, language

    async def get_or_create(self, user_id, job_hash, language, version=None) -> AdaptedCV:
        """Reutilizar adaptacion existente o crear nueva. Soporta versionado."""
```

### 8.4 Versionado de CVs Adaptados

Cada adaptacion es inmutable — se crea una nueva version si el usuario regenera:

```python
# Obtener ultima version
GET /api/v1/cv/adapt/{job_hash}?latest=true

# Obtener version especifica
GET /api/v1/cv/adapt/{job_hash}?version=2

# Comparar versiones
GET /api/v1/cv/adapt/{job_hash}/diff?v1=1&v2=2

# Reusar adaptacion para rol parecido
POST /api/v1/cv/adapt/reuse
{
    "source_job_hash": "abc123",  # Job original
    "target_job_hash": "def456",  # Nuevo job similar
    "language": "de"
}
```

### 8.5 Exportacion

- **PDF**: weasyprint o reportlab, formato profesional suizo (A4, sin foto por defecto)
- **HTML**: printable, para copiar a email
- **JSON Resume**: formato estandar (jsonresume.org) para interoperabilidad
- **Cover letter separada**: PDF independiente

### 8.6 Costes

| Operacion | Modelo Groq | Tokens estimados | Coste |
|-----------|-------------|------------------|-------|
| Adaptacion CV + Cover Letter | Llama 3.1 70B Versatile | ~3000 input + ~3000 output | Gratuito (tier free) |
| Re-ranking (referencia) | Llama 3.1 8B Instant | ~1500 input + ~500 output | Gratuito |

Con tier gratuito de Groq (14,400 req/dia), un usuario puede adaptar ~20 CVs/dia.

---

## 9. Integracion de Portales

### 9.1 Fase 1 — APIs Directas (semana 1-2)

Reutilizar patron `BaseJobProvider` + `BaseJobCache` de ReactPortfolio:

| Portal | Patron | Esfuerzo | Jobs Estimados |
|--------|--------|----------|----------------|
| **12 APIs actuales** | Copiar providers existentes | 1 dia | ~5,000 |
| **Jooble** | REST POST, nuevo provider | 2 horas | ~2,000 (Suiza) |
| **Careerjet** | REST GET + cliente Python | 2 horas | ~3,000 (Suiza) |

**Total Fase 1:** ~10,000 jobs unicos (con dedup por hash)

### 9.2 Fase 2 — APIs con Credenciales (semana 3-4)

| Portal | Patron | Esfuerzo | Jobs Estimados |
|--------|--------|----------|----------------|
| **Job-Room (SECO)** | REST + Basic Auth | 1 dia (+ tramite) | ~15,000 |
| **Talent.com** | Publisher API | 2 dias (+ partnership) | ~5,000 |

**Total acumulado:** ~30,000 jobs

### 9.3 Fase 3 — Scraping Selectivo (semana 5-8)

**Solo portales con TOS verificados como compatibles:**

| Portal | Metodo | Esfuerzo | Jobs Estimados | TOS OK |
|--------|--------|----------|----------------|--------|
| **stelle.admin.ch** | Playwright headless | 3 dias | ~500 | Si (publico) |
| **med-jobs.ch** | aiohttp + BeautifulSoup | 2 dias | ~800 | Verificar |
| **Gastrojob.ch** | aiohttp + BeautifulSoup | 2 dias | ~400 | Verificar |
| **Financejobs.ch** | aiohttp + BeautifulSoup | 1 dia | ~300 | Verificar |
| **myScience.ch** | aiohttp + BeautifulSoup | 1 dia | ~200 | Verificar |

**Total acumulado:** ~32,000 jobs

### 9.4 Arquitectura de Scraping

```python
# services/scraper_engine.py
class BaseScraper(ABC):
    """Base para scrapers sin API. Se ejecuta en Celery worker."""

    rate_limit: float = 2.0          # segundos entre requests (minimo)
    max_pages: int = 10
    user_agent: str = "SwissJobHunter/1.0 (+https://swissjobhunter.ch/bot)"

    async def pre_check(self) -> bool:
        """Verificar compliance antes de scrapear."""
        compliance = await get_source_compliance(self.source_key)
        return compliance.is_allowed and compliance.robots_txt_ok

    @abstractmethod
    async def parse_listing_page(self, html: str) -> List[RawJob]: ...

    @abstractmethod
    async def parse_job_detail(self, html: str) -> JobDetail: ...

    async def scrape(self) -> List[ProcessedJob]:
        if not await self.pre_check():
            logger.warning(f"Scraping disabled for {self.source_key}")
            return []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            # ... rate-limited pagination ...
```

---

## 10. Scheduler + Workers

### 10.1 Arquitectura: APScheduler → Celery

APScheduler solo despacha tareas a Celery workers. No ejecuta trabajo pesado inline.

```python
# services/scheduler.py — DISPATCHER (ligero, en FastAPI process)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = AsyncIOScheduler()

# APIs rapidas: cada 30 minutos → Celery task
scheduler.add_job(
    lambda: celery_app.send_task("tasks.update_api_sources"),
    CronTrigger(minute="*/30"), id="api_sources"
)

# Scrapers: cada 6 horas → Celery task
scheduler.add_job(
    lambda: celery_app.send_task("tasks.update_scraper_sources"),
    CronTrigger(hour="*/6"), id="scraper_sources"
)

# AI matching incremental: cada hora → Celery task
scheduler.add_job(
    lambda: celery_app.send_task("tasks.run_incremental_matching"),
    CronTrigger(minute="15"), id="ai_matching"
)

# Alertas email: diario 8:00 CET → Celery task
scheduler.add_job(
    lambda: celery_app.send_task("tasks.send_daily_digests"),
    CronTrigger(hour=8, timezone="Europe/Zurich"), id="daily_digest"
)

# Health check URLs: semanal domingo 3:00 → Celery task
scheduler.add_job(
    lambda: celery_app.send_task("tasks.check_job_urls"),
    CronTrigger(day_of_week="sun", hour=3), id="url_health"
)

# Dedup semantica batch: diario 4:00 → Celery task
scheduler.add_job(
    lambda: celery_app.send_task("tasks.dedup_semantic_batch"),
    CronTrigger(hour=4), id="semantic_dedup"
)

# Limpieza: semanal (jobs >30 dias inactivos)
scheduler.add_job(
    lambda: celery_app.send_task("tasks.cleanup_stale_jobs"),
    CronTrigger(day_of_week="sun", hour=3, minute=30), id="cleanup"
)
```

### 10.2 Matching Incremental (Celery task)

```python
@celery_app.task
async def run_incremental_matching():
    """Match new jobs against active user profiles."""
    new_jobs = await get_jobs_since(last_run_timestamp)
    if not new_jobs:
        return

    profiles = await get_active_profiles_with_embeddings()

    for profile in profiles:
        # Stage 1: Embedding similarity (batch, rapido)
        candidates = embedding_match(profile.cv_embedding, new_jobs, threshold=0.4)

        # Stage 2: LLM re-rank top candidates
        if candidates:
            ranked = await llm_rerank(profile, candidates[:50])
            await save_match_results(profile.user_id, ranked)

            # Notify si score supera umbral del usuario
            if ranked[0].score_final > profile.min_alert_score:
                await notify_user(profile.user_id, ranked[:10])
```

### 10.3 Alert Fatigue Control

Para evitar saturar al usuario con notificaciones:

```python
class AlertController:
    MAX_PUSH_PER_DAY = 5           # Maximo push notifications/dia
    MIN_SCORE_FOR_PUSH = 75        # Solo push si score > 75
    DIGEST_GROUP_BY = "company"    # Agrupar por empresa en digests

    async def should_notify(self, user_id, match_score, channel):
        if channel == "push":
            sent_today = await count_push_today(user_id)
            if sent_today >= self.MAX_PUSH_PER_DAY:
                return False  # Acumular para digest
            if match_score < self.MIN_SCORE_FOR_PUSH:
                return False  # Solo alta relevancia por push
        return True

    async def build_digest(self, user_id):
        """Agrupar matches por empresa/rol para digest legible."""
        matches = await get_unnotified_matches(user_id)
        grouped = group_by(matches, key=self.DIGEST_GROUP_BY)
        return render_digest_template(grouped)
```

---

## 11. Frontend Mobile-First (Capacitor + PWA)

### 11.1 Onboarding Wizard (nuevo — ausente en plan original)

Sin onboarding, los usuarios abandonan antes de ver valor. Flujo en 4 pasos:

```
Paso 1: Upload CV              Paso 2: Confirmar Skills
┌─────────────────────┐        ┌─────────────────────┐
│  Welcome to          │        │  We found these     │
│  SwissJobHunter!     │        │  skills in your CV: │
│                      │        │                     │
│  📄 Upload your CV   │        │  ✅ Python          │
│  (PDF or DOCX)       │        │  ✅ FastAPI         │
│                      │        │  ✅ PostgreSQL      │
│  [📷 Camera]         │        │  ☐ React (add?)    │
│  [📁 File]           │        │                     │
│  [⌨️ Manual]         │        │  [+ Add skill]      │
│                      │        │                     │
│  [Next →]            │        │  [← Back] [Next →]  │
└─────────────────────┘        └─────────────────────┘

Paso 3: Preferencias            Paso 4: Primera Busqueda
┌─────────────────────┐        ┌─────────────────────┐
│  Your preferences:   │        │  🎉 Finding matches │
│                      │        │  for you...         │
│  📍 Location:        │        │                     │
│  [Zurich     ▼]     │        │  ┌───────────────┐  │
│  📏 Radius: 30km    │        │  │ Job Card      │  │
│                      │        │  │ Score: 87%    │  │
│  🏠 Remote: [Hybrid]│        │  └───────────────┘  │
│                      │        │  ┌───────────────┐  │
│  💰 Salary min:      │        │  │ Job Card      │  │
│  [100,000 CHF/year] │        │  │ Score: 82%    │  │
│                      │        │  └───────────────┘  │
│  🌐 Languages:       │        │                     │
│  [EN] [DE] [FR]     │        │  Found 47 matches!  │
│                      │        │                     │
│  [← Back] [Start!]  │        │  [Go to Matches →]  │
└─────────────────────┘        └─────────────────────┘
```

### 11.2 Navegacion: Sidebar + Bottom Nav

```
DESKTOP (>1024px)                    TABLET (768-1024px)
┌─────┬──────────────────┐          ┌──────────────────────┐
│     │                  │          │ ☰ ← hamburger menu   │
│ 🔍 │  Contenido       │          │                      │
│ 🤖 │  2-3 columnas    │          │  Contenido           │
│ 📋 │                  │          │  2 columnas          │
│ 👤 │                  │          │                      │
│ ⚙️ │                  │          │                      │
└─────┴──────────────────┘          └──────────────────────┘

MOBILE (<768px)
┌──────────────────────┐
│  Contenido 100vw     │  ← Pull-to-refresh
│  1 columna           │  ← Scroll vertical infinito
│  ┌────────────────┐  │
│  │   Job Card     │  │  ← Swipe left = descartar
│  │   touch 44px+  │  │  ← Swipe right = guardar
│  └────────────────┘  │
├──────────────────────┤
│ 🔍  🤖  📋  👤  ⚙️  │  ← Bottom tab bar (safe area)
└──────────────────────┘
```

### 11.3 Paginas y Tabs

| Tab | Icono | Ruta | Pagina | Mobile UX |
|-----|-------|------|--------|-----------|
| **Buscar** | 🔍 | `/search` | Busqueda unificada + filtros | Filtros en bottom sheet, infinite scroll, filtro por radio geografico |
| **Matches** | 🤖 | `/matches` | AI matching + feedback | Swipe derecha=thumbs up, izquierda=thumbs down (estilo Tinder) |
| **Pipeline** | 📋 | `/pipeline` | Kanban de candidaturas | Columnas en horizontal scroll, cards arrastrables con touch |
| **Perfil** | 👤 | `/profile` | CV, skills, preferencias | Formulario vertical, upload CV con camara/galeria, score weight sliders |
| **Mas** | ⚙️ | `/more` | Alertas, insights, config | Lista con chevron (patron iOS Settings) |

**Sub-paginas:**

| Ruta | Pagina | Acceso desde |
|------|--------|-------------|
| `/` | Landing (solo no-auth) | URL directa |
| `/auth` | Login / Register | Landing o redirect |
| `/onboarding` | Wizard 4 pasos | Post-registro |
| `/job/:hash` | Detalle de job (full screen) | Tap en cualquier job card |
| `/job/:hash/adapt` | CV adaptado + cover letter | Boton "Adapt CV" en detalle |
| `/more/saved` | Busquedas guardadas | Tab "Mas" |
| `/more/alerts` | Configuracion de alertas | Tab "Mas" |
| `/more/insights` | Market insights + metricas personales | Tab "Mas" |
| `/more/settings` | Cuenta, idioma, tema, GDPR | Tab "Mas" |
| `/more/privacy` | Exportar/eliminar datos (GDPR) | Tab "Mas" > Settings |

### 11.4 Kanban con Auto-Transiciones

El pipeline no es solo drag & drop — tiene automatizaciones:

| Accion del usuario | Transicion automatica |
|-------------------|----------------------|
| Click "Save" en job card | → `saved` |
| Click "Apply" o generar CV adaptado | → `applied` (con timestamp `applied_at`) |
| Abrir link externo para aplicar | → `applied` (con URL destino registrada) |
| Recibir respuesta (manual) | → `phone_screen` / `technical` / `rejected` |

Stats de conversion visibles en el pipeline:
- `saved → applied`: X%
- `applied → interview`: X%
- `interview → offer`: X%
- Tiempo medio de respuesta por empresa

### 11.5 Patrones Mobile-First

| Patron | Implementacion | Justificacion |
|--------|---------------|---------------|
| **Bottom sheet** | Filtros, detalle rapido, acciones | Accesible con pulgar |
| **Swipe gestures** | Like/dislike en matches, dismiss alertas | Interaccion natural, una mano |
| **Pull-to-refresh** | Todas las listas | Patron universal mobile |
| **Infinite scroll** | Resultados de busqueda y matches | Evita paginacion numerica |
| **Sticky header** | Titulo + acciones principales | Contexto siempre visible |
| **Touch targets 44px+** | Todos los botones e iconos | Apple HIG / Material Design |
| **Safe area insets** | Bottom nav respeta notch | `env(safe-area-inset-bottom)` |
| **Skeleton loading** | Placeholders animados en cards | Percepcion de velocidad |
| **Haptic feedback** | Swipe completado, accion confirmada | `@capacitor/haptics` (nativo) o `navigator.vibrate()` (web fallback) |

### 11.6 Capacitor — App Nativa (iOS + Android)

Capacitor envuelve la SPA React en un shell nativo. El mismo codigo React se compila como:
- **Web:** `vite build` → SPA estatica (con PWA Service Worker como fallback)
- **iOS:** `npx cap sync ios` → proyecto Xcode con WKWebView
- **Android:** `npx cap sync android` → proyecto Android Studio con WebView

```
frontend/
├── src/                    ← Codigo React compartido (web + mobile)
├── ios/                    ← Proyecto Xcode (generado por Capacitor)
│   └── App/
│       ├── AppDelegate.swift
│       └── capacitor.config.json
├── android/                ← Proyecto Android Studio (generado por Capacitor)
│   └── app/
│       ├── src/main/
│       └── capacitor.config.json
├── capacitor.config.ts     ← Configuracion Capacitor
├── vite.config.js
└── package.json
```

```typescript
// capacitor.config.ts
import { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'ch.swissjobhunter.app',
  appName: 'SwissJobHunter',
  webDir: 'dist',              // Output de vite build
  server: {
    // En desarrollo: apuntar al dev server de Vite
    // url: 'http://localhost:5173',
    // cleartext: true,
  },
  plugins: {
    PushNotifications: {
      presentationOptions: ['badge', 'sound', 'alert'],
    },
    SplashScreen: {
      launchAutoHide: true,
      androidScaleType: 'CENTER_CROP',
    },
  },
};

export default config;
```

**Plugins Capacitor utilizados:**

| Plugin | Paquete | Uso |
|--------|---------|-----|
| **Push Notifications** | `@capacitor/push-notifications` | Push nativas via APNs (iOS) y FCM (Android) — sin limitaciones Web Push |
| **Camera** | `@capacitor/camera` | Upload CV desde camara o galeria con UX nativa |
| **SQLite** | `@capacitor-community/sqlite` | Offline DB local — sin limite 50MB de PWA, datos persistentes |
| **Preferences** | `@capacitor/preferences` | Key-value store para settings y tokens |
| **Haptics** | `@capacitor/haptics` | Feedback tactil nativo (impact, notification, selection) |
| **Share** | `@capacitor/share` | Compartir ofertas via share sheet nativo |
| **Biometric** | `capacitor-native-biometric` | Face ID / fingerprint para login rapido |
| **App** | `@capacitor/app` | Detectar background/foreground, deep links, back button Android |
| **StatusBar** | `@capacitor/status-bar` | Control de color/estilo de status bar |
| **Keyboard** | `@capacitor/keyboard` | Control del teclado virtual (auto-scroll, eventos) |
| **Network** | `@capacitor/network` | Detectar estado de conexion para offline mode |
| **Background Task** | `@capawesome/capacitor-background-task` | Sync periodico en background |

**Abstraccion web/nativo — patron recomendado:**

```typescript
// hooks/usePushNotifications.ts
import { Capacitor } from '@capacitor/core';
import { PushNotifications } from '@capacitor/push-notifications';

export function usePushNotifications() {
  const isNative = Capacitor.isNativePlatform();

  async function register() {
    if (isNative) {
      // Push nativo via APNs/FCM
      const permission = await PushNotifications.requestPermissions();
      if (permission.receive === 'granted') {
        await PushNotifications.register();
      }
    } else {
      // Fallback: Web Push API
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.subscribe({ ... });
      await sendSubscriptionToBackend(subscription);
    }
  }

  // ... listeners para foreground/background/action
  return { register, isNative };
}
```

```typescript
// hooks/useOfflineStorage.ts
import { Capacitor } from '@capacitor/core';

export function useOfflineStorage() {
  const isNative = Capacitor.isNativePlatform();

  if (isNative) {
    // SQLite nativo — sin limite de almacenamiento
    return new SQLiteStorage();
  } else {
    // Fallback: IndexedDB via Workbox
    return new IndexedDBStorage();
  }
}
```

### 11.7 PWA (web fallback)

La PWA se mantiene como fallback para acceso via browser (desktop y mobile sin instalar app nativa). En mobile, la experiencia principal es la app Capacitor.

```javascript
// vite.config.js — solo activo para build web, no para Capacitor
VitePWA({
  registerType: 'autoUpdate',
  manifest: {
    name: 'SwissJobHunter',
    short_name: 'JobHunter',
    display: 'standalone',
    orientation: 'portrait',
    theme_color: '#0f172a',
    background_color: '#0f172a',
    start_url: '/search',
  },
  workbox: {
    runtimeCaching: [
      { urlPattern: /\/api\/v1\/jobs/, handler: 'StaleWhileRevalidate' },
      { urlPattern: /\/api\/v1\/match/, handler: 'NetworkFirst' },
    ],
  },
})
```

**Capacidades web (PWA fallback):**
- Instalable en home screen (desktop Chrome, Safari)
- Offline basico: ultimos N matches en IndexedDB (limite ~50MB en iOS Safari)
- Push via Web Push API (con limitaciones iOS)
- Service Worker para cache de assets

**Offline conflict resolution (compartido web + nativo):**
- Queue de acciones offline (feedback, status changes, notes)
- Al recuperar conexion: sync secuencial con timestamp-based conflict resolution
- Si conflicto: ultima escritura gana + notificar al usuario

### 11.8 Accesibilidad (a11y)

> No es opcional — es requisito legal en muchos contextos suizos.

| Requisito | Implementacion |
|-----------|---------------|
| **ARIA labels** | Todos los elementos interactivos tienen labels descriptivos |
| **Keyboard navigation** | Tab order logico, focus visible, skip links |
| **Screen reader** | Contenido de cards accesible, score anunciado como texto |
| **Contraste** | Score badges con ratio >= 4.5:1 (WCAG AA) |
| **Focus management** | Swipe gestures tienen alternativa de boton; focus trap en modals |
| **Reduced motion** | Respetar `prefers-reduced-motion` — desactivar animaciones swipe |
| **Semantic HTML** | `<main>`, `<nav>`, `<article>` para cards, `<section>` para tabs |

### 11.9 Componentes Reutilizables de ReactPortfolio

| Componente | Origen | Adaptacion |
|-----------|--------|------------|
| `JobCard` | JobCardExtras.jsx | + swipe actions, + AI score badge, + skill match chips |
| `KanbanBoard` | KanbanWindow.jsx | @dnd-kit touch sensors, horizontal scroll, auto-transiciones |
| `FilterPanel` | JobFilterWindow.jsx | Bottom sheet, salary slider, location autocomplete + radio |
| `SkillsChart` | JobMarketAnalyticsWindow.jsx | Chart.js responsive, touch zoom |
| `SSENotifications` | useSSENotifications.js | + Capacitor Push nativo (mobile) + Web Push fallback (web) + alert fatigue |

### 11.10 Nuevos Componentes

- **OnboardingWizard**: 4 pasos (CV → Skills → Preferencias → Primera busqueda)
- **BottomTabBar**: 5 tabs con badge de notificaciones, safe area (`@capacitor/status-bar`)
- **BottomSheet**: Sheet deslizable (filtros, acciones, detalle rapido)
- **SwipeableJobCard**: Gestos laterales + animacion Framer Motion + `@capacitor/haptics`
- **CVUploader**: `@capacitor/camera` para foto nativa (mobile) o drag & drop (desktop)
- **MatchExplainer**: Card expandible con desglose del scoring + skills analysis
- **ScoreWeightSliders**: 5 sliders touch-friendly para ajustar pesos
- **AlertConfigPanel**: Frecuencia, score minimo, max push/dia — toggles iOS
- **MarketInsights**: Graficas con touch zoom + metricas personales del pipeline
- **CVAdaptViewer**: Vista del CV adaptado + cover letter + diff entre versiones
- **InterviewPrepCard**: Preguntas probables + flashcards de skills (post-MVP)
- **PlatformGate**: Wrapper que detecta `Capacitor.isNativePlatform()` para UX condicional

---

## 12. API Endpoints

### 12.1 Auth
```
POST   /api/v1/auth/register         — Registro con email (requiere GDPR consent)
POST   /api/v1/auth/login            — JWT access + refresh
POST   /api/v1/auth/refresh           — Renovar access token
POST   /api/v1/auth/oauth/google     — OAuth2 con Google
GET    /api/v1/auth/me               — Usuario actual
```

### 12.2 Profile
```
GET    /api/v1/profile               — Obtener perfil completo
PUT    /api/v1/profile               — Actualizar perfil (skills, preferencias, score_weights)
POST   /api/v1/profile/cv            — Subir CV (PDF/DOCX) → parse + embedding
DELETE /api/v1/profile/cv            — Eliminar CV
GET    /api/v1/profile/skills-market — Comparativa skills vs demanda del mercado
GET    /api/v1/profile/export        — Exportar todos los datos (GDPR portabilidad)
DELETE /api/v1/profile/delete-all    — Eliminar cuenta y todos los datos (GDPR olvido)
```

### 12.3 Jobs
```
GET    /api/v1/jobs/search           — Busqueda unificada con filtros (texto + estructurados)
GET    /api/v1/jobs/all              — Todos los jobs paginados
GET    /api/v1/jobs/{hash}           — Detalle de un job
GET    /api/v1/jobs/stats            — Stats agregadas (por source, location, skill, sector)
GET    /api/v1/jobs/sources          — Lista de sources activas + status + compliance
```

**Filtros de busqueda (query params):**
```
q             — Texto libre (full-text search PostgreSQL tsvector)
source        — Filtrar por fuente (comma-separated)
remote_only   — Solo remotos
canton        — Filtrar por canton suizo (comma-separated)
city          — Filtrar por ciudad
radius_km     — Radio en km desde ciudad (requiere city)
language      — Idioma de la oferta (de/fr/en/it)
seniority     — junior/mid/senior/lead
contract_type — full_time/part_time/contract
salary_min    — Salario minimo (CHF anual normalizado)
salary_max    — Salario maximo
sector        — healthcare/finance/hospitality/tech/education/government
sort          — newest/oldest/salary/relevance (relevance usa embedding score)
limit/offset  — Paginacion
```

### 12.4 AI Match
```
POST   /api/v1/match/analyze         — Ejecutar matching (manual o por saved search)
GET    /api/v1/match/results         — Resultados recientes del usuario
POST   /api/v1/match/{id}/feedback   — Feedback explicito (thumbs_up/down)
POST   /api/v1/match/{id}/implicit   — Feedback implicito (view_time, opened, dismissed)
GET    /api/v1/match/status          — Estado del modelo + ultima ejecucion
```

### 12.5 CV Adaptation
```
POST   /api/v1/cv/adapt              — Generar CV adaptado + cover letter para un job
GET    /api/v1/cv/adapt/{job_hash}   — Obtener adaptacion existente (latest o version)
GET    /api/v1/cv/adapt/{job_hash}/diff — Comparar versiones
POST   /api/v1/cv/adapt/reuse        — Reusar adaptacion para job similar
POST   /api/v1/cv/adapt/export-pdf   — Exportar CV adaptado como PDF
POST   /api/v1/cv/adapt/export-html  — Exportar como HTML printable
```

### 12.6 Applications (Kanban)
```
GET    /api/v1/applications          — Listar candidaturas del usuario
POST   /api/v1/applications          — Crear candidatura (desde job card)
PATCH  /api/v1/applications/{id}     — Actualizar status/notas/follow_up_date
DELETE /api/v1/applications/{id}     — Eliminar candidatura
GET    /api/v1/applications/stats    — Stats: conversion rates, avg response time, by source
```

### 12.7 Saved Searches
```
GET    /api/v1/searches              — Listar busquedas guardadas
POST   /api/v1/searches              — Crear busqueda + configurar alerta
PUT    /api/v1/searches/{id}         — Actualizar filtros/frecuencia
DELETE /api/v1/searches/{id}         — Eliminar
POST   /api/v1/searches/{id}/run     — Ejecutar ahora (bypass scheduler)
```

### 12.8 Notifications
```
GET    /api/v1/notifications/stream  — SSE (new_matches, search_complete, job_update)
GET    /api/v1/notifications/history — Historial de notificaciones
PUT    /api/v1/notifications/{id}/read — Marcar como leida
POST   /api/v1/notifications/push/subscribe   — Registrar push subscription
DELETE /api/v1/notifications/push/unsubscribe — Eliminar push subscription
```

### 12.9 Admin (protegido)
```
GET    /api/v1/admin/sources         — Status de todos los providers/scrapers
PATCH  /api/v1/admin/sources/{key}   — Activar/desactivar source (kill-switch)
GET    /api/v1/admin/metrics         — Metricas agregadas (jobs/source, latencia, errores)
GET    /api/v1/admin/compliance      — Estado de compliance por fuente
GET    /api/v1/admin/health          — Health check general
```

---

## 13. Observabilidad y Operaciones

### 13.1 Metricas (Prometheus + Grafana)

| Metrica | Tipo | Descripcion |
|---------|------|-------------|
| `jobs_fetched_total{source}` | Counter | Jobs obtenidos por fuente |
| `jobs_duplicates_total{source}` | Counter | Duplicados detectados por fuente |
| `provider_latency_seconds{source}` | Histogram | Latencia por provider/scraper |
| `provider_errors_total{source,type}` | Counter | Errores por fuente y tipo |
| `provider_status{source}` | Gauge | 1=ok, 0=error, -1=disabled |
| `matching_duration_seconds` | Histogram | Duracion del pipeline IA |
| `cv_adaptations_total` | Counter | Adaptaciones generadas |
| `active_users_daily` | Gauge | Usuarios activos diarios |
| `applications_by_status{status}` | Gauge | Candidaturas por estado |

**Alerting rules:**
- Provider sin jobs nuevos en 24h → alert
- Error rate > 50% en provider → alert + considerar kill-switch
- Latencia de matching > 30s → alert
- Queue de Celery > 100 tareas pendientes → alert

### 13.2 Structured Logging (Loki)

```python
# Todos los logs en JSON con correlation ID
{
    "timestamp": "2026-02-24T10:30:00Z",
    "level": "INFO",
    "service": "job_aggregator",
    "source": "jooble",
    "action": "fetch_jobs",
    "jobs_found": 142,
    "duplicates": 23,
    "duration_ms": 1200,
    "correlation_id": "abc-123-def"
}
```

### 13.3 Panel Operativo Admin

Dashboard con:
- Status de cada provider (verde/amarillo/rojo)
- Jobs nuevos/hora por fuente (grafica 24h)
- Duplicados detectados por nivel (exact/fuzzy/semantic)
- Cola de Celery: tareas pendientes/en proceso/completadas
- Compliance: proximas revisiones de TOS
- Uso de Groq API: requests/dia vs limite (14,400)

---

## 14. Testing y CI/CD

### 14.1 Testing Strategy

| Tipo | Herramienta | Cobertura |
|------|------------|-----------|
| **Unit tests** | pytest + pytest-asyncio | Cada provider/scraper con fixtures JSON/HTML |
| **Integration tests** | TestContainers (PostgreSQL + Redis) | Pipeline completo: ingest → normalize → dedup → match |
| **Contract tests** | VCR.py / responses | APIs externas (Jooble, Careerjet) con cassettes grabadas |
| **E2E tests (web)** | Playwright | Frontend web/PWA: onboarding, search, swipe, kanban |
| **E2E tests (mobile)** | Detox o Appium | Capacitor builds: push nativas, camera, offline, biometrics |
| **Performance tests** | locust | Carga: 100 usuarios concurrentes buscando + matching |
| **Security tests** | bandit + safety | Vulnerabilidades en codigo Python + dependencias |

### 14.2 CI/CD (GitHub Actions)

```yaml
# .github/workflows/ci.yml
on: [push, pull_request]
jobs:
  lint:
    - ruff check backend/
    - eslint frontend/
  test-backend:
    services: [postgres, redis]
    steps:
      - pytest backend/tests/ --cov --cov-fail-under=80
  test-frontend:
    - vitest run --coverage
  security:
    - bandit -r backend/
    - safety check
    - npm audit
  build-web:
    - docker build backend/
    - vite build frontend/
  build-mobile:   # builds nativos Capacitor
    - npx cap sync ios && xcodebuild (archive)
    - npx cap sync android && ./gradlew assembleRelease
  lighthouse:
    - lighthouse-ci (PWA score >= 90, a11y >= 90)
  migration-check:
    - alembic check (no pending migrations)
  deploy-preview:  # solo en PR
    - deploy web to preview URL
  deploy-prod:     # solo en main
    - deploy web to production
    - upload iOS build to TestFlight (manual trigger)
    - upload Android build to Play Console (manual trigger)
```

**Nota sobre builds nativos:** Los builds iOS requieren macOS runner (GitHub Actions tiene `macos-latest`). Los builds Android funcionan en Linux. Para MVP, los uploads a App Store/Play Store pueden ser manuales — automatizar con Fastlane post-MVP.

---

## 15. Plan de Implementacion por Fases

### Fase 0: Scaffolding + Fundamentos (2 semanas)

```
swissjobhunter/
├── backend/
│   ├── alembic/
│   ├── core/              ← JWT, security, config, GDPR
│   ├── models/            ← SQLAlchemy models (User, Job, etc.)
│   ├── schemas/           ← Pydantic v2
│   ├── routers/           ← FastAPI routers
│   ├── services/          ← Business logic
│   │   ├── job_service.py       ← BaseJobCache, BaseJobProvider
│   │   ├── data_quality.py      ← Normalizacion + dedup
│   │   ├── job_matcher.py       ← AI pipeline 2 etapas
│   │   ├── cv_parser.py         ← PDF/DOCX → text + skills
│   │   ├── cv_adapter.py        ← CV adaptation + cover letter
│   │   ├── scraper_engine.py    ← Base scraper + Playwright
│   │   ├── sse_manager.py       ← SSE pub/sub user-scoped
│   │   ├── alert_controller.py  ← Alert fatigue control
│   │   ├── circuit_breaker.py   ← Circuit breakers
│   │   └── compliance.py        ← TOS engine + kill-switch
│   ├── providers/         ← Job source providers (1 por fuente)
│   ├── scrapers/          ← Scraper implementations
│   ├── tasks/             ← Celery tasks
│   ├── tests/
│   ├── config.py
│   ├── database.py
│   ├── celery_app.py
│   ├── main.py
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/         ← Incluye abstracciones web/nativo (usePushNotifications, useOfflineStorage)
│   │   ├── stores/        ← Zustand stores
│   │   ├── config/
│   │   └── i18n/          ← de, fr, en
│   ├── ios/               ← Proyecto Xcode (generado por Capacitor)
│   ├── android/           ← Proyecto Android Studio (generado por Capacitor)
│   ├── capacitor.config.ts ← Configuracion Capacitor
│   ├── vite.config.js
│   └── Dockerfile          ← Solo para build web
├── docker-compose.yml     ← PostgreSQL + Redis + backend + worker + frontend (web)
├── .env.example
├── .github/workflows/ci.yml
└── README.md
```

**Tareas Fase 0:**

*Area: Monorepo + Infraestructura* (completado 2026-02-24)
- [x] Inicializar monorepo con backend + frontend
- [x] Configurar Docker Compose (PostgreSQL + pgvector + Redis + app + Celery worker)
- [x] Configurar Alembic async (asyncpg + async_engine_from_config)
- [x] Configurar Celery + Redis como broker (3 queues: default, scraping, ai)
- [x] CI: GitHub Actions (lint + tests + docker compose build)
- [x] Health checks: `/health` + `/api/v1/health`
- [x] Frontend placeholder: React 19 + Vite + TailwindCSS v4 + proxy /api

*Area: Auth + Seguridad* (completado 2026-02-25)
- [x] Modelos User/UserProfile (SQLAlchemy async + Alembic migration)
- [x] Auth JWT: register, login, refresh, me (access 30min + refresh 7d)
- [x] Rate limiting con slowapi (5/min auth, 100/min general, Redis backend)
- [x] 18 tests passing (health + auth + edge cases)
- [ ] OAuth2 Google (diferido a fase posterior)

*Area: Compliance (GDPR/nDSG)* (completado 2026-02-25)
- [x] Modelo SourceCompliance (kill-switch, rate limits, TOS tracking, auto-disable)
- [x] Alembic migration: tabla source_compliance
- [x] ComplianceEngine service: can_scrape, report_block (3 strikes → kill-switch), reset_blocks, get_compliance_status
- [x] GDPR endpoints: GET /api/v1/profile/export (portabilidad), DELETE /api/v1/profile/delete-all (derecho al olvido)
- [x] 18 tests passing (compliance engine + profile export/delete)

*Area: Capacitor* (completado 2026-02-25)
- [x] Capacitor init: @capacitor/core + @capacitor/cli, capacitor.config.ts
- [x] Plugins base: push-notifications, camera, preferences, haptics, network, status-bar
- [x] Abstracciones web/nativo: usePushNotifications, useOfflineStorage, useCamera (Capacitor.isNativePlatform())
- [ ] Generar proyectos nativos ios/ + android/ (requiere Xcode/Android Studio, diferido)

*Area: Servicios base* (completado 2026-02-25)
- [x] job_service.py: BaseJobCache (TTL, in-memory) + BaseJobProvider (ABC con fetch, normalize, hash)
- [x] circuit_breaker.py: CircuitBreaker (CLOSED→OPEN→HALF_OPEN, configurable threshold/timeout)
- [x] sse_manager.py: SSEManager (user-scoped channels, broadcast, format_sse)
- [x] job_matcher.py: JobMatcher (embedding, salary, location, recency, LLM scoring con pesos configurables)
- [x] 36 tests passing (cache, provider, circuit breaker, SSE, matcher)

### Fase 1: Motor de Datos + Calidad (3 semanas)

**Tareas:**
- [ ] Migrar los 12 providers existentes de ReactPortfolio a `providers/`
- [ ] Implementar provider Jooble (REST POST)
- [ ] Implementar provider Careerjet (REST GET + cliente Python)
- [ ] Modelo `Job` en DB con pgvector + campos normalizados
- [ ] Implementar `DataNormalizer`: salario→CHF, ubicacion→canton, idioma, seniority, contrato
- [ ] Implementar dedup 3 niveles (exact + fuzzy + semantic batch)
- [ ] Scheduler: APScheduler → Celery dispatch (cada 30 min APIs)
- [ ] Endpoint `/api/v1/jobs/search` con filtros completos + full-text search PostgreSQL
- [ ] Endpoint `/api/v1/jobs/stats`
- [ ] Health check de URLs (Celery task semanal)
- [ ] Frontend: pagina de busqueda con filtros + job cards + infinite scroll
- [ ] Tests: 1 test file por provider + integration tests con fixtures

### Fase 2: Perfiles + AI Matching + CV Adapter (3 semanas)

**Tareas:**
- [ ] Modelo UserProfile + endpoints CRUD
- [ ] CV parser: PDF/DOCX → texto plano → skills extraction
- [ ] Embedding del perfil con `paraphrase-multilingual-MiniLM-L12-v2`
- [ ] Embedding incremental de jobs nuevos (Celery task)
- [ ] Etapa 1: cosine similarity batch via pgvector
- [ ] Etapa 2: Groq LLM re-ranking con explicaciones y desglose de scoring
- [ ] Modelo AIMatchResult + endpoints (results, feedback explicito + implicito)
- [ ] CV Adapter service: adaptacion + cover letter completa
- [ ] Versionado de CVs adaptados + reutilizacion para roles similares
- [ ] Exportacion PDF/HTML
- [ ] Frontend: pagina AI Matches + MatchExplainer + feedback buttons + swipe gestures
- [ ] Frontend: pagina CV Adapt viewer + diff entre versiones
- [ ] Score weight sliders en perfil
- [ ] Tests: pipeline completo con mocks

### Fase 3: Kanban + Alertas + Onboarding (2 semanas)

**Tareas:**
- [ ] Modelo JobApplication + endpoints CRUD + stats
- [ ] Frontend: Kanban drag & drop con @dnd-kit + auto-transiciones
- [ ] Apply tracking: URL destino, timestamp, outcome por fuente
- [ ] Modelo SavedSearch + endpoints + filtro por radio geografico
- [ ] Scheduler: matching automatico por saved search (Celery)
- [ ] SSE notifications user-scoped para nuevos matches
- [ ] Alert fatigue control: max push/dia, agrupacion, digests
- [ ] Email notifications (digests diarios/semanales con Jinja2 templates)
- [ ] Web Push API (pywebpush backend + subscription flow frontend)
- [ ] Onboarding wizard (4 pasos: CV → Skills → Preferencias → Primera busqueda)
- [ ] Frontend: busquedas guardadas + configuracion alertas
- [ ] Tests: scheduler + notifications + Kanban

### Fase 4: Scraping + Portales Adicionales (3 semanas)

**Tareas:**
- [ ] `scraper_engine.py` base con Playwright + rate limiting + compliance check
- [ ] Scraper: stelle.admin.ch (JS rendering, Playwright)
- [ ] Scraper: med-jobs.ch (aiohttp + BeautifulSoup)
- [ ] Scraper: Gastrojob.ch
- [ ] Scraper: Financejobs.ch
- [ ] Scraper: myScience.ch
- [ ] Contactar SECO para acceso a Job-Room API
- [ ] Contactar Talent.com para publisher API
- [ ] Admin panel: activar/desactivar sources, compliance status, metricas
- [ ] Tests: scrapers con HTML fixtures + contract tests

### Fase 4b: Company Watcher — Monitor de Paginas de Empleo Corporativas (2 semanas)

Funcionalidad para monitorizar las paginas de empleo de empresas especificas. Muchas empresas suizas publican ofertas primero en su propia web antes de pagar portales. Este modulo detecta cambios en esas paginas y notifica al usuario cuando aparecen nuevas ofertas.

**Flujo:**
1. El usuario anade una empresa (nombre + URL de pagina de empleos + selector CSS opcional)
2. Primera visita: se guarda un snapshot (contenido HTML + hash + ofertas extraidas)
3. Celery task periodico (configurable: cada 6h, 12h, 24h) re-visita la pagina
4. Compara con snapshot anterior → detecta ofertas nuevas
5. Notifica via SSE / push / email segun preferencias del usuario

**Modelo `WatchedCompany`:**
```python
class WatchedCompany(Base):
    __tablename__ = "watched_companies"

    id: UUID PK
    user_id: UUID FK → users.id
    company_name: str           # "Roche", "SBB", "Nestlé"
    careers_url: str            # URL de la pagina de empleos
    css_selector: str | None    # Selector CSS para zona de ofertas (opcional)
    check_frequency_hours: int  # 6, 12, 24 (default 24)
    is_active: bool             # default True
    last_check_at: datetime | None
    last_content_hash: str | None   # SHA-256 del contenido relevante
    last_change_at: datetime | None
    consecutive_errors: int     # Para circuit breaker
    notify_email: bool          # default True
    notify_push: bool           # default True
    created_at: datetime
    updated_at: datetime
```

**Modelo `WatchedCompanyJob`:**
```python
class WatchedCompanyJob(Base):
    __tablename__ = "watched_company_jobs"

    id: UUID PK
    watched_company_id: UUID FK → watched_companies.id
    title: str
    url: str | None             # Link directo a la oferta
    description_snippet: str | None
    first_seen_at: datetime
    is_new: bool                # True hasta que el usuario lo vea
```

**Deteccion de cambios (3 estrategias):**
1. **ATS conocidos** — Presets para Greenhouse, Lever, Workday, SAP SuccessFactors, SmartRecruiters (CSS selectors predefinidos, JSON APIs internas si las exponen)
2. **Selector CSS custom** — El usuario o admin configura un selector para la zona de listings
3. **Diff generico** — Si no hay selector, se hashea el contenido de la pagina y se hace diff de links que contengan patrones de empleo (`/jobs/`, `/careers/`, `/stelle/`, `/emploi/`)

**Tareas:**
- [ ] Modelos: `WatchedCompany` + `WatchedCompanyJob` (Alembic migration)
- [ ] Servicio `company_watcher.py`: fetch pagina, extraer ofertas, comparar con snapshot, detectar nuevas
- [ ] Presets ATS: selectores CSS predefinidos para Greenhouse, Lever, Workday, SAP SuccessFactors
- [ ] Heuristicas de auto-deteccion: buscar listas de links con patrones `/jobs/`, `/careers/`, `/stelle/`, `/emploi/`
- [ ] Celery task `check_watched_companies` (queue `scraping`): ejecutar segun frecuencia configurada por empresa
- [ ] Integracion con CircuitBreaker (por dominio) y ComplianceEngine (robots.txt, rate limits)
- [ ] Router `/api/v1/watched-companies`: CRUD + historial de ofertas detectadas + toggle activo/inactivo
- [ ] Notificaciones: SSEManager (tiempo real) + email digest (opcional)
- [ ] Frontend: pagina de gestion de empresas monitorizadas + feed de ofertas nuevas
- [ ] Tests: mock HTTP responses + deteccion de cambios + notificaciones

**Limites y seguridad:**
- Maximo 20 empresas por usuario (plan free), 50 (premium)
- Rate limit: 1 request por dominio cada 10 segundos minimo
- Respetar robots.txt (integracion con ComplianceEngine)
- Timeout 15s por pagina, skip si pagina requiere JS complejo (Playwright solo para ATS conocidos)
- Si 5 errores consecutivos → desactivar auto y notificar al usuario

### Fase 5: Capacitor + PWA + Polish + Observabilidad + Deploy (3 semanas)

**Capacitor — builds nativos:**
- [ ] Configurar `capacitor.config.ts` con appId, plugins, server
- [ ] Build iOS: `npx cap sync ios`, abrir en Xcode, configurar signing, testar en simulador
- [ ] Build Android: `npx cap sync android`, abrir en Android Studio, testar en emulador
- [ ] Configurar push nativas: APNs certificates (iOS) + Firebase Cloud Messaging (Android)
- [ ] Integrar `@capacitor-community/sqlite` para offline robusto
- [ ] Integrar `@capacitor/camera` para upload CV nativo
- [ ] Integrar `capacitor-native-biometric` para login rapido
- [ ] Integrar `@capawesome/capacitor-background-task` para sync periodico
- [ ] Testar en dispositivos reales: iPhone (iOS 16+), Android (API 24+)
- [ ] Preparar assets: iconos (1024x1024), splash screens, screenshots para stores
- [ ] Subir a TestFlight (iOS) y Play Console beta track (Android)

**PWA (web fallback):**
- [ ] Configurar vite-plugin-pwa: manifest, Service Worker, offline caching (solo para web)
- [ ] Verificar que PWA funciona como fallback en desktop browsers

**UX polish (compartido web + nativo):**
- [ ] Bottom tab bar con badge + safe area insets
- [ ] Bottom sheet para filtros y acciones
- [ ] Pull-to-refresh + skeleton loading (shimmer)
- [ ] Haptics nativos en swipe/acciones (`@capacitor/haptics`)

**Accesibilidad:**
- [ ] ARIA labels, keyboard navigation, screen reader support
- [ ] Contraste WCAG AA en badges
- [ ] `prefers-reduced-motion` support
- [ ] Semantic HTML

**Observabilidad:**
- [ ] Prometheus metricas por provider
- [ ] Grafana dashboards (operativo + admin)
- [ ] Structured JSON logging con Loki
- [ ] Alerting rules

**i18n:**
- [ ] Aleman, frances, ingles (idiomas oficiales suizos + ingles)

**Deploy:**
- [ ] Docker Compose produccion (Nginx + Certbot SSL) — backend + web
- [ ] Deploy backend a VPS (Hetzner Cloud Zurich / Railway)
- [ ] Publicar en Apple App Store (requiere Apple Developer $99/ano)
- [ ] Publicar en Google Play Store (requiere Google Play $25 unico)
- [ ] Landing page con valor + registro + links a App Store/Play Store
- [ ] Documentacion API (auto-generada por FastAPI)
- [ ] README completo

---

## 16. Que se Reutiliza de ReactPortfolio

| Componente | Archivo Original | Lineas | Adaptacion |
|-----------|-----------------|--------|-----------|
| BaseJobCache + BaseJobProvider | `services/job_service.py` | 179 | + persistencia DB + embeddings |
| 12 job providers | `services/job_provider.py` | 491 | Separar en `providers/` (1 por fuente) |
| AI Matcher (2 etapas) | `services/job_matcher.py` | 234 | + multilingual embeddings, feedback loop, scoring configurable, pgvector |
| Circuit breakers | `services/circuit_breaker.py` | 97 | Copiar tal cual |
| SSE Manager | `services/sse_manager.py` | 56 | + user-scoped channels + alert fatigue |
| Groq Service | `services/groq_service.py` | ~80 | + modelo 70B para CV adaptation |
| Busqueda unificada | `routers/jobs_unified.py` | 168 | + filtros (canton, idioma, radio, seniority, sector) + full-text search |
| AI Match endpoints | `routers/ai_match.py` | 121 | + persistencia + feedback explicito/implicito |
| Kanban CRUD | `routers/job_applications.py` | 119 | + auto-transiciones + stats + apply tracking |
| Saved searches | `routers/saved_searches.py` | 145 | + scheduler integration + alert fatigue |
| CV Export | `routers/cv_export.py` | 215 | Adaptar para CV dinamico + cover letter |
| JWT Auth | `core/security.py`, `routers/auth.py` | ~120 | + OAuth2 social + GDPR consent |
| Job sources config | `config/jobSources.js` | 285 | + nuevas fuentes + compliance status |
| Job sort/pagination | `hooks/useJobBoardControls.js` | 85 | + infinite scroll + sort by ai_score/salary |
| Job cards | `components/JobCardExtras.jsx` | 127 | + swipe + AI score badge + skill chips |
| **Total reutilizable** | | **~2400** | **~60% del backend core** |

## 17. Que es Nuevo (no existe en ReactPortfolio)

| Feature | Descripcion |
|---------|------------|
| **Compliance Engine** | Matriz TOS por fuente, kill-switch, GDPR/nDSG, cifrado CVs |
| **Data Quality Pipeline** | Normalizacion (salario CHF, canton, idioma, seniority), dedup 3 niveles |
| **Celery Workers** | Tareas pesadas fuera del event loop (embeddings, scraping, LLM, digests) |
| **Multilingual Embeddings** | `paraphrase-multilingual-MiniLM-L12-v2` para DE/FR/EN/IT |
| **Feedback Implicito** | Signals de comportamiento (view time, open, dismiss) ajustan scoring |
| **CV Adapter + Cover Letter** | Adaptacion de CV completa + carta de presentacion + versionado |
| **Onboarding Wizard** | 4 pasos: CV → Skills → Preferencias → Primera busqueda |
| **Alert Fatigue Control** | Max push/dia, score minimo, agrupacion por empresa, digests inteligentes |
| **Capacitor Native Shell** | App nativa real para iOS (App Store) y Android (Play Store) desde la misma base React. Plugins nativos: push, camera, SQLite, biometrics, haptics, background sync |
| **Mobile-first UI** | Bottom tab bar, bottom sheets, swipe gestures, touch 44px+, safe area |
| **PWA (web fallback)** | `display: standalone`, offline basico, Web Push — fallback para desktop/browser |
| **Kanban Auto-transiciones** | Aplicar → auto-move a `applied`, con URL destino y timestamp |
| **Apply Tracking Real** | URL destino, timestamp, outcome, metricas de conversion por fuente |
| **Filtro Geografico por Radio** | Busqueda por km desde ciudad con geocoding |
| **Accesibilidad (a11y)** | ARIA, keyboard nav, screen reader, WCAG AA contraste |
| **Observabilidad** | Prometheus + Grafana + Loki, metricas por provider, alerting |
| **Panel Admin** | Status sources, compliance, metricas, kill-switch |
| **Testing Completo** | Unit + Integration + Contract + E2E + Performance + Security |
| **CI/CD** | GitHub Actions: lint, test, security, Lighthouse, preview deploys |
| **i18n** | Aleman, frances, ingles |
| **Multi-usuario** | Perfiles independientes, datos aislados por user_id |
| **Job Persistence** | PostgreSQL + pgvector (no solo cache en memoria) |
| **Health Checks URLs** | Verificacion semanal de que las ofertas siguen activas |
| **OAuth2 Social** | Login con Google/GitHub ademas de email+password |

---

## 18. Estimacion de Esfuerzo

| Fase | Duracion | Horas | Notas |
|------|----------|-------|-------|
| Fase 0: Scaffolding + Fundamentos | 2 semanas | 50h | Incluye compliance + Celery + auth + Capacitor init |
| Fase 1: Motor de Datos + Calidad | 3 semanas | 55h | APIs + normalizacion + dedup 3 niveles |
| Fase 2: Perfiles + AI + CV Adapter | 3 semanas | 60h | Matching multilingual + CV + cover letter |
| Fase 3: Kanban + Alertas + Onboarding | 2 semanas | 40h | Pipeline automatizado + alert fatigue |
| Fase 4: Scraping + Portales | 3 semanas | 45h | Playwright + SECO + admin panel |
| Fase 4b: Company Watcher | 2 semanas | 30h | Monitor paginas empleo corporativas + notificaciones |
| Fase 5: Capacitor + PWA + Polish + Ops + Deploy | 3 semanas | 50h | Builds nativos, App Store, a11y, observabilidad |
| **Total MVP** | **18 semanas** | **~330h** | |
| Post-MVP features (seccion 20) | +4 semanas | +60h | Planes, interview prep, A/B, integraciones |

**vs plan original:** +90h y +5 semanas, pero incluye: app nativa real en App Store/Play Store (Capacitor), compliance (obligatorio), calidad de datos (critico), Celery (evita reescritura), observabilidad (produccion), a11y (legal), y Company Watcher (ventaja competitiva: detectar ofertas directas antes que aparezcan en portales). La inversion extra en Capacitor se compensa con push nativas sin limitaciones, presencia en stores, y offline robusto — factores criticos para retencion mobile.

---

## 19. Consideraciones de Produccion

### 19.1 Costes Estimados (tier minimo)

| Servicio | Coste/mes | Notas |
|----------|-----------|-------|
| VPS (Hetzner CX21, Zurich) | ~5 EUR | 2 vCPU, 4GB RAM |
| Dominio .ch | ~12 EUR/ano | Switch registrar |
| Apple Developer Program | ~8.25 EUR (~$99/ano) | Obligatorio para App Store |
| Google Play Console | ~2 EUR (unico $25) | Pago unico, amortizado |
| Groq API (free tier) | 0 EUR | 14,400 requests/dia, Llama 3.1 70B |
| Email (Resend free) | 0 EUR | 3,000 emails/mes |
| Firebase (push Android) | 0 EUR | FCM es gratuito |
| **Total mensual** | **~15 EUR** | Primer ano. ~13 EUR/mes a partir del segundo |

### 19.2 Limites a Considerar

- **Groq free tier**: 14,400 req/dia → suficiente para ~200 usuarios con 70 matches/dia
- **Multilingual MiniLM**: CPU-only → ~0.5s por batch de 100 jobs (aceptable, en Celery worker)
- **Scraping rate limits**: Minimo 2s entre requests, robots.txt, kill-switch automatico
- **pgvector**: similarity search nativa en PostgreSQL (evita calcular en Python)
- **Redis**: Cache + broker Celery + rate limiting + SSE pub/sub
- **Celery workers**: 2 workers en VPS de 4GB es suficiente para MVP

### 19.3 Escalabilidad Futura

1. **Fase 1 (1-100 usuarios)**: Docker Compose en VPS, 2 Celery workers
2. **Fase 2 (100-1000)**: Separar DB (PostgreSQL dedicado), 4+ workers, CDN para frontend
3. **Fase 3 (1000+)**: Kubernetes, vector DB dedicada (Qdrant), GPU para embeddings, Celery auto-scaling

---

## 20. Post-MVP Features (Prioridad Baja)

Features para implementar despues del lanzamiento, segun traccion y feedback:

### 20.1 Modelo de Negocio — Planes y Limites

| Feature | Free | Pro (~9 CHF/mes) |
|---------|------|------------------|
| Busquedas guardadas | 3 | Ilimitadas |
| CV adaptations/mes | 5 | Ilimitadas |
| Alertas | Daily digest | Realtime push |
| Matching priority | Normal queue | Priority queue |
| Historial de matches | 30 dias | Ilimitado |
| Exportar metricas | No | Si |

### 20.2 Interview Preparation Assistant

- Generar preguntas de entrevista probables basadas en la oferta
- Flashcards de skills tecnicas requeridas
- Resumen de empresa (scraping del sitio corporativo)
- Consejos especificos para entrevistas en Suiza (cultura, expectativas)

### 20.3 A/B Testing y Experimentacion

- Panel de experimentos para probar:
  - Diferentes scoring weights por defecto
  - Prompts de LLM para re-ranking y CV adaptation
  - UX de swipe vs botones para feedback
- Medir: apply rate, engagement, retention, conversion

### 20.4 Integraciones Externas

- Export a **Google Calendar** para entrevistas programadas
- Export a **Notion/Trello** para quienes prefieran gestionar alli
- Sync con **Google Sheets** para tracking manual
- **Webhook** generico para integraciones custom

### 20.5 Marketplace de Plantillas

- Templates de CV por sector (salud, finanzas, hosteleria, tech, gobierno)
- Templates de cover letter por idioma y formalidad
- Templates creados por la comunidad (UGC)

### 20.6 Market Insights Avanzados

- Tendencias de salarios por canton/sector/seniority
- Skills mas demandadas (trending up/down)
- Jobs publicados/semana por sector (estacionalidad)
- Benchmark: "tu perfil es competitivo para X% de ofertas en tu sector"
- Tiempo medio de contratacion por empresa (crowdsourced de aplicaciones)

---
---

# ANEXO TECNICO: Codigo Reutilizable + Nuevas Implementaciones

## T1. Agregacion y Filtrado de Ofertas

### T1.1 Backend Reutilizable

#### `services/job_service.py` — Base comun (179 lineas, copiar y adaptar)

```python
# REUTILIZAR TAL CUAL
class BaseJobCache:
    """Cache en memoria con TTL, estado de actualizacion, y rate de exito."""
    cache_duration: int
    recent_jobs: List[Dict]
    last_update_time: float
    update_in_progress: bool
    last_error: str | None
    success_rate: float

    def is_cache_stale(max_age_seconds) -> bool
    def get_cache_age() -> str
    def reset_cache()

# REUTILIZAR TAL CUAL
async def fetch_with_retry(session, url, headers, params,
    max_retries=3, backoff_factor=1.0, timeout=15.0) -> Optional[Any]:
    """HTTP GET con retry exponencial."""

# REUTILIZAR TAL CUAL
def strip_html_tags(text) -> str
def process_job_location(country) -> str
def extract_job_skills(title, desc) -> List[str]
```

**Adaptacion:** Cambiar `BaseJobCache` para persistir en PostgreSQL ademas de memoria. Anadir campo `embedding` al schema.

#### `services/job_provider.py` — Strategy pattern (491 lineas, copiar y refactorizar)

```python
# SCHEMA UNIFICADO (reutilizar + expandir)
{
    "id": str,
    "title": str,
    "company": str,
    "location": str,
    "country": str,
    "canton": str,              # NUEVO — normalizado
    "url": str,
    "date": str,
    "remote": bool,
    "tags": List[str],
    "source": str,
    "salary_min": int | None,
    "salary_max": int | None,
    "salary_currency": str,
    "salary_min_chf": int | None,  # NUEVO — normalizado a CHF anual
    "salary_max_chf": int | None,  # NUEVO
    "salary_period": str,          # NUEVO — yearly/monthly/hourly
    "language": str,               # NUEVO — detectado con langdetect
    "seniority": str,              # NUEVO — extraido del titulo
    "contract_type": str,          # NUEVO — normalizado
    "description": str,
    "description_snippet": str,
    "logo": str | None,
    "employment_type": str,
    "_hash": str,
}

# ABSTRACT BASE (reutilizar)
class BaseJobProvider(ABC):
    def get_source_name(self) -> str
    def get_cache(self) -> BaseJobCache
    def normalize_job(self, raw) -> dict
    def get_all_jobs(self) -> list
    def get_stats(self) -> dict

# 12 providers existentes → copiar como providers/
# Nuevos: JoobleProvider, CareerjetProvider, JobRoomProvider
```

#### `services/circuit_breaker.py` — (97 lineas, copiar tal cual)

```python
class CircuitBreaker:
    """CLOSED → OPEN (tras N fallos) → HALF_OPEN (tras timeout)."""
    failure_threshold: int = 5
    recovery_timeout: int = 60
    async def call(self, coro)
    def get_status(self) -> dict
```

#### `services/data_quality.py` — NUEVO

```python
class DataNormalizer:
    """Normaliza campos de jobs a formato consistente."""

    CANTON_MAP = { "zurich": "ZH", "zürich": "ZH", ... }  # 26 cantones, variantes DE/FR/EN/IT
    SALARY_RATES = { "EUR": 0.96, "USD": 0.88, "GBP": 1.12 }  # → CHF

    async def normalize_job(self, raw_job: dict) -> dict:
        """Aplica todas las normalizaciones."""

    def _normalize_salary(self, job, field) -> int | None:
        """Convierte a CHF anual."""

    def _normalize_location(self, location: str) -> str | None:
        """Mapea a canton."""

    def _detect_language(self, text: str) -> str:
        """langdetect sobre descripcion."""

    def _extract_seniority(self, title: str) -> str | None:
        """Regex para extraer nivel del titulo."""

    def _normalize_contract(self, job: dict) -> str | None:
        """Mapea a ContractType enum."""

class Deduplicator:
    """Deduplicacion en 3 niveles."""

    def dedup_exact(self, job) -> str: ...
    def dedup_fuzzy(self, job) -> str: ...
    async def dedup_semantic_batch(self, new_jobs, existing_embeddings) -> List: ...
```

#### `services/compliance.py` — NUEVO

```python
class ComplianceEngine:
    """Verifica TOS antes de scrapear. Kill-switch automatico."""

    async def can_scrape(self, source_key: str) -> bool:
        """Verificar is_allowed + robots_txt_ok."""

    async def report_block(self, source_key: str, status_code: int):
        """Registrar bloqueo. Si 3 consecutivos → kill-switch."""

    async def get_compliance_status(self) -> List[dict]:
        """Status de todas las fuentes para admin panel."""
```

### T1.2 Frontend Reutilizable

#### `config/jobSources.js` — (285 lineas, copiar y expandir)

```javascript
// REUTILIZAR + expandir con nuevas fuentes y compliance status
export const JOB_SOURCES = [
  {
    key: 'jobicy',
    color: '#00e5ff',
    urlPath: '/api/v1/jobs/search?source=jobicy',
    compliance: 'api',  // NUEVO
  },
  // ... + jooble, careerjet, job_room, scrapers
];
```

#### `hooks/useJobBoardControls.js` — (85 lineas, copiar y adaptar)

**Adaptacion mobile:** reemplazar paginacion numerica por infinite scroll. Anadir sort by `ai_score` y `salary`.

---

## T2. Pipeline de IA: Matching + Feedback

### T2.1 Backend Reutilizable

#### `services/job_matcher.py` — (234 lineas, copiar y evolucionar)

```python
# REUTILIZAR — Core del matching
class JobMatcher:
    _model = None  # SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")

    def _build_job_text(self, job):
        """title + company + description + skills → texto para embedding."""

    # EVOLUCION — perfil dinamico por usuario
    async def find_matches(self, jobs, user_profile, top_k=50):
        """Usa user_profile.cv_embedding en vez de archivo fijo."""
        profile_embedding = user_profile.cv_embedding or await self._encode(user_profile.cv_text)
        # pgvector similarity search en DB
        ...

    # EVOLUCION — scoring multi-factor
    async def compute_final_score(self, user_profile, job, embedding_score, ai_score):
        weights = user_profile.score_weights or DEFAULT_WEIGHTS
        return (
            weights['embedding'] * embedding_score +
            weights['llm'] * (ai_score / 100) +
            weights['salary'] * salary_match(user_profile, job) +
            weights['location'] * location_match(user_profile, job) +
            weights['recency'] * recency_score(job.date)
        )

# NUEVO — Feedback implicito
class ImplicitFeedbackCollector:
    async def record(self, user_id, job_hash, action, duration_ms=None):
        """Registrar signal implicita."""

    async def compute_adjustment(self, user_id) -> dict:
        """Calcular ajuste de pesos basado en signals acumuladas."""
```

#### `services/groq_service.py` — (copiar y expandir)

```python
class GroqService:
    client: Groq
    default_model = "llama-3.1-8b-instant"      # Rapido para re-ranking
    powerful_model = "llama-3.1-70b-versatile"   # Para CV adaptation

    async def get_chat_response(self, user_message, system_prompt,
                                 model=None, temperature=0.2, max_tokens=2048):
        """Llama a Groq API via threadpool."""
```

### T2.2 Frontend Reutilizable

- `AIJobMatchWindow.jsx` → pagina de resultados con swipe + MatchExplainer
- `useDashboardData.js` → hook multi-fetch paralelo
- `useSSENotifications.js` → + Web Push API + alert fatigue

---

## T3. Kanban Pipeline + Busquedas Guardadas

### T3.1 Backend Reutilizable

#### `routers/job_applications.py` — (119 lineas, copiar y expandir)

```python
# REUTILIZAR — CRUD completo
@router.get("/")        # Listar candidaturas
@router.post("/")       # Crear (desde card o match)
@router.patch("/{id}")  # Actualizar status/notas
@router.delete("/{id}") # Eliminar

# NUEVO — Auto-transiciones
async def auto_transition(user_id, job_hash, trigger: str):
    """Mover automaticamente segun accion."""
    if trigger == "adapt_cv":
        await update_status(user_id, job_hash, "applied")
    elif trigger == "open_external_link":
        await update_status(user_id, job_hash, "applied")

# NUEVO — Stats con conversion y tracking real
@router.get("/stats")
async def pipeline_stats(user_id):
    return {
        "by_status": { "saved": 12, "applied": 8, ... },
        "conversion_rates": { "saved_to_applied": 0.67, ... },
        "avg_days_to_response": 4.2,
        "by_source": { "jooble": {"applied": 3, "offer": 1}, ... },
    }
```

#### `routers/saved_searches.py` — (145 lineas, copiar y expandir)

```python
# REUTILIZAR — CRUD
# NUEVO — scheduler integration + alert fatigue
class SavedSearch(Base):
    # campos existentes +
    notify_frequency: Enum(realtime, daily, weekly)
    notify_email: bool
    notify_push: bool
    last_run_at: DateTime
    total_matches_found: int

@router.post("/{id}/run")
async def run_saved_search(search_id, user_id):
    """Ejecutar con filtros guardados + AI matching."""
```

#### `services/sse_manager.py` — (56 lineas, copiar y adaptar)

```python
# EVOLUCION — canales por usuario
class SSEManager:
    connections: Dict[int, Set[asyncio.Queue]]  # user_id → queues
    async def subscribe(user_id) -> Queue
    async def broadcast_to_user(user_id, event_type, data)
    # Eventos: new_matches, search_complete, job_update
```

### T3.2 Frontend Reutilizable

- `KanbanWindow.jsx` → + @dnd-kit touch sensors + horizontal scroll + auto-transiciones
- `SavedSearchesWindow.jsx` → + scheduler status + alert config
- `BookmarkedJobsWindow.jsx` → migrar a DB (no localStorage)

---

## T4. CV Adapter + Cover Letter (NUEVO)

### T4.1 Servicio Backend

```python
# services/cv_adapter.py — NUEVO (~150 lineas)
CV_ADAPT_SYSTEM_PROMPT = """Expert career consultant for the Swiss job market.
Rules: never fabricate, reorder by relevance, rewrite summary, highlight matching skills,
explain transferable skills, output in requested language (de/fr/en).
Output: JSON with profile, experience[], skills[], education[], languages[], cover_letter."""

class CVAdapter:
    async def adapt_cv(self, user_profile, job, language="en", match_result=None) -> dict:
        """Generate tailored CV + cover letter. Runs in Celery worker."""

    async def get_or_create(self, user_id, job_hash, language, version=None) -> AdaptedCV:
        """Reutilizar existente o crear nueva version."""

    def _parse_adaptation(self, response: str, language: str) -> dict:
        """Parse JSON from LLM response."""
```

### T4.2 Router

```python
# routers/cv_adapt.py — NUEVO (~100 lineas)
@router.post("/adapt")           # Generar CV adaptado + cover letter
@router.get("/adapt/{job_hash}") # Obtener adaptacion (latest o version)
@router.get("/adapt/{job_hash}/diff")  # Comparar versiones
@router.post("/adapt/reuse")     # Reusar para job similar
@router.post("/adapt/export-pdf")   # PDF profesional A4
@router.post("/adapt/export-html")  # HTML printable
```

### T4.3 Flujo UX Mobile

```
1. Match card → tap "Adapt CV"
2. Bottom sheet: elegir idioma (EN/DE/FR) → "Generate"
3. Loading en Celery worker (~5s)
4. Resultado full-screen:
   - Profile reescrito
   - Skills highlighted (match ✅ / transferable 🔄 / gap ❌)
   - Cover letter completa (3-4 parrafos)
   - Sugerencias adicionales
5. Acciones: [📥 PDF] [📧 HTML] [✏️ Edit] [📋 → Pipeline "applied"]
```

---

## T5. Resumen de Archivos

| Archivo ReactPortfolio | Lineas | Accion | Destino SwissJobHunter |
|----------------------|--------|--------|----------------------|
| `services/job_service.py` | 179 | Copiar + adaptar (DB + embeddings) | `services/job_service.py` |
| `services/job_provider.py` | 491 | Copiar + separar | `providers/*.py` |
| `services/job_matcher.py` | 234 | Copiar + evolucionar (multilingual, feedback, pgvector) | `services/job_matcher.py` |
| `services/circuit_breaker.py` | 97 | Copiar tal cual | `services/circuit_breaker.py` |
| `services/sse_manager.py` | 56 | Copiar + user-scoped + alert fatigue | `services/sse_manager.py` |
| `services/groq_service.py` | ~80 | Copiar + modelo 70B | `services/groq_service.py` |
| `routers/jobs_unified.py` | 168 | Copiar + expandir filtros | `routers/jobs.py` |
| `routers/ai_match.py` | 121 | Copiar + persistencia + feedback | `routers/match.py` |
| `routers/job_applications.py` | 119 | Copiar + auto-transition + stats | `routers/applications.py` |
| `routers/saved_searches.py` | 145 | Copiar + scheduler + alert fatigue | `routers/searches.py` |
| `routers/cv_export.py` | 215 | Copiar + adaptar CV dinamico | `routers/cv.py` |
| `config/jobSources.js` | 285 | Copiar + expandir fuentes | `config/jobSources.js` |
| `hooks/useJobBoardControls.js` | 85 | Copiar + infinite scroll | `hooks/useJobControls.js` |
| `components/JobCardExtras.jsx` | 127 | Copiar + swipe + AI badges | `components/JobCard.jsx` |
| **Total reutilizado** | **~2400** | | **~55% del backend** |
| | | | |
| **NUEVO** `services/cv_adapter.py` | ~150 | Crear | CV adaptation + cover letter |
| **NUEVO** `services/cv_parser.py` | ~100 | Crear | PDF/DOCX → text + skills |
| **NUEVO** `services/data_quality.py` | ~200 | Crear | Normalizacion + dedup 3 niveles |
| **NUEVO** `services/compliance.py` | ~80 | Crear | TOS engine + kill-switch |
| **NUEVO** `services/alert_controller.py` | ~60 | Crear | Alert fatigue control |
| **NUEVO** `services/scheduler.py` | ~80 | Crear | APScheduler → Celery dispatch |
| **NUEVO** `services/scraper_engine.py` | ~150 | Crear | Base scraper + Playwright |
| **NUEVO** `routers/cv_adapt.py` | ~100 | Crear | Endpoints CV adaptation |
| **NUEVO** `routers/admin.py` | ~80 | Crear | Admin panel endpoints |
| **NUEVO** `tasks/*.py` | ~200 | Crear | Celery tasks (matching, scraping, digests) |
| **NUEVO** frontend components | ~800 | Crear | Onboarding, swipe, bottom sheet, etc. |
| **Total nuevo** | **~2000** | | **~45% del total** |
