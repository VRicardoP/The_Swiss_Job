# Módulo de vigilancia de empleo — Colegios británicos en Suiza
## Arquitectura completa, fuentes, scraping y plan de acción automatizado

---

## Arquitectura por capas

```
┌─────────────────────────────────────────────────────────────────────────┐
│  CAPA 1 — DATA SOURCES                                                  │
│                                                                         │
│  ┌──────────────────┐ ┌─────────────────┐ ┌───────────┐ ┌───────────┐  │
│  │ School websites  │ │ Company portals │ │ Job boards│ │ LinkedIn  │  │
│  │   17 targets     │ │  Nord Anglia    │ │TES · COBIS│ │SchoolSprg │  │
│  └──────────────────┘ └─────────────────┘ └───────────┘ └───────────┘  │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  CAPA 2 — COLLECTION ENGINE                                             │
│                                                                         │
│  ┌─────────────────────┐ ┌──────────────────────┐ ┌──────────────────┐  │
│  │   HTTP + parse      │ │  Headless browser    │ │  API · RSS · JSON│  │
│  │ requests + BS4      │ │     Playwright       │ │  TES · SchoolSprg│  │
│  └─────────────────────┘ └──────────────────────┘ └──────────────────┘  │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  CAPA 3 — INTELLIGENCE                                                  │
│                                                                         │
│  ┌─────────────────────┐ ┌──────────────────────┐ ┌──────────────────┐  │
│  │  Change detection   │ │   Role classifier    │ │  Urgency scorer  │  │
│  │   hash + diff       │ │ keywords + exclusions│ │ policy + calendar│  │
│  └─────────────────────┘ └──────────────────────┘ └──────────────────┘  │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
┌──────────────────────────────────▼──────────────────────────────────────┐
│  CAPA 4 — ACTION LAYER                                                  │
│                                                                         │
│  ┌─────────────────────┐ ┌──────────────────────┐ ┌──────────────────┐  │
│  │      Notify         │ │    Draft letter      │ │Calendar + action │  │
│  │  push + email + log │ │   template A or B    │ │deadline + follow-│  │
│  └─────────────────────┘ └──────────────────────┘ └──────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Foco de vigilancia — Fuentes por prioridad y método

| Fuente | Método | Frecuencia | Urgencia alerta | Colegios cubiertos |
|---|---|---|---|---|
| **Ecolint jobs page** ⚠️ Sin alerta nativa | requests + BS4 | 3× / día | 🔴 Inmediata | ISG (3 campus) |
| **ISB recruitment@** ⚠️ No guarda candidaturas | Monitor web + respuesta | 2× / día | 🔴 Inmediata | ISB |
| **TES Jobs** | RSS feed nativo | Cada hora | 🟠 Alta | Todos (17) |
| **SchoolSpring** | API / requests | 3× / día | 🟠 Alta | ZIS (exclusivo) |
| **Nord Anglia portal** | Playwright (JS-rendered) | 2× / día | 🟠 Alta | Beau Soleil, LCIS, Champittet |
| **Páginas individuales — Grupo A (6)** | requests + BS4 / Playwright según estructura | 2× / día | 🟠 Alta | ZIS, ISG, ISB, ISCS, Beau Soleil, VIS |
| **Páginas individuales — Grupo B/C (11)** | requests + BS4 | 1× / día | 🟡 Normal | Resto de colegios |
| **COBIS Global Jobs** | requests + BS4 | 2× / día | 🟡 Normal | Todos los miembros COBIS |
| **LinkedIn Jobs** | RSS guardado / Playwright autenticado | 1× / día | 🟡 Normal | Todos |
| **jobs.ch / jobscout24** | RSS / requests | 1× / día | 🟡 Normal | ISB, colegios Basilea/Zúrich |

---

## Plan de acción automatizado — Cuando se detecta una nueva oferta

### Paso 1 — Detectar y extraer
- Hash SHA-256 del bloque de vacantes.
- Si hash ≠ anterior → extraer título, descripción, fecha, contacto y URL de la oferta.
- Guardar snapshot HTML para diff legible.
- ⏱ < 2 segundos · automático

### Paso 2 — Clasificar el rol
Keyword match en título + descripción.

**MATCH (score +1):**
`IT Manager` · `Technology Coordinator` · `Systems Administrator` · `Head of IT` · `EdTech` · `ICT` · `Digital Learning Coordinator`

**EXCLUDE (score −1):**
`IT teacher` · `informatics teacher` · `computer science teacher` · `ICT teacher` · `professor`

**PARTIAL (score +0.5):**
`Technology` · `Digital` · `Support` (sin rol explícito)

- ⏱ < 1 segundo · automático

### Paso 3 — Puntuar urgencia (0–100)

| Condición | Puntos |
|---|---|
| Colegio Grupo A | +30 |
| Colegio NO guarda candidaturas (ISB) | +20 |
| Ventana pico (ene–abr, oct–nov) | +15 |
| Texto contiene "immediate" o "as soon as possible" | +20 |
| Fecha límite explícita en < 7 días | +10 |
| Colegio ZIS (solo portal, no candidatura espontánea) | −20 |

**Umbral de disparo: ≥ 50 → notificación inmediata. < 50 → digest diario.**

- ⏱ < 1 segundo · automático

### Paso 4 — Disparar notificación
Push inmediato con:
- Nombre del colegio + título exacto del rol
- URL directa a la oferta
- Score de urgencia
- Plantilla de carta sugerida (A o B)
- Contacto objetivo (de la base de datos del módulo)
- Email digest si score < 50

- ⏱ < 5 segundos · automático

### Paso 5 — Pre-rellenar borrador de carta y email
- Seleccionar plantilla A (colegio grande/urbano) o B (internado/pequeño/remoto) según perfil en la base de datos.
- Pre-rellenar: nombre del contacto, escuela, fecha, asunto del email.
- Abrir en el editor de la app para revisión humana.
- **⚠️ NUNCA enviar automáticamente. Revisión humana obligatoria.**

### Paso 6 — Crear eventos de calendario
- **Evento 1:** "Candidatura [colegio]" → fecha detección + 2 días hábiles.
- **Evento 2:** "Follow-up [colegio]" → fecha detección + 14 días.
- **Evento 3:** Si hay deadline explícita → evento 3 días antes.
- Exportar como `.ics` o integrar con Google Calendar API.

- ⏱ automático · integrable con Google Calendar / iCal

### Paso 7 — Registrar en base de datos de seguimiento
Entrada en tracking DB: `colegio · rol · fecha_detección · score · estado`

**Estados posibles:**
```
detected → draft_ready → sent → awaiting_response →
follow_up_due → interview → closed_positive / closed_negative
```

---

## Árbol de decisión post-clasificación

```
NUEVA OFERTA DETECTADA
│
├─ classify() → ¿rol IT relevante?
│   ├─ NO  → log("irrelevant") · no notificar
│   └─ SÍ  → score_urgency()
│       │
│       ├─ score < 50  → email digest (acumulado diario)
│       └─ score ≥ 50  → notificación inmediata push
│           │
│           ├─ school.policy == "direct_email_ok"          (ISB)
│           │   → draft_letter(template="A",
│           │                  contact="balazs.szegedi@isbasel.ch")
│           │   → calendar("Enviar candidatura ISB", now+2d)
│           │
│           ├─ school.policy == "portal_only"              (ZIS, Ecolint)
│           │   → open_portal_url(school.portal_url)
│           │   → calendar("Aplicar en portal ZIS", now+1d)
│           │   → notify("⚠️ Solo portal — no email directo")
│           │
│           └─ school.policy == "direct_email_director"    (VIS, BSB)
│               → draft_letter(template="B",
│                              contact="grant.ferguson@vischool.ch")
│               → calendar("Enviar candidatura VIS", now+2d)
```

---

## Estructura de datos por oferta detectada

```python
{
  "source":           "TES",
  "school":           "International School Basel",
  "school_id":        "isb",          # clave en tabla de metadatos
  "title":            "IT Manager",
  "url":              "https://...",
  "date_detected":    "2026-05-28",
  "date_posted":      "2026-05-26",   # si disponible
  "description_snippet": "...",       # primeros 500 chars
  "content_hash":     "sha256:...",
  "role_score":       1.0,            # clasificador
  "urgency_score":    75,             # 0–100
  "template":         "A",           # carta A o B
  "status":           "detected",
  "raw_html":         "..."           # para diff legible
}
```

---

## Tabla de metadatos de colegios (tabla estática — base del módulo)

> Esta tabla se construye una vez y se mantiene manualmente. Es la pieza que hace el módulo inteligente.

| school_id | Nombre | Grupo | Policy | Contacto | Template | Portal URL | Método scraping | Selector CSS vacantes |
|---|---|---|---|---|---|---|---|---|
| zis | Zurich International School | A | portal_only | hr@zis.ch | A | zurichinternational.schoolspring.com | SchoolSpring API | `.jobs-list` |
| isg | International School of Geneva | A | portal_only | via web form | A | ecolint.ch/en/job-opportunities | requests+BS4 | `.job-listing` |
| isb | International School Basel | A | direct_email_ok | recruitment@isbasel.ch | A | isbasel.ch/join/working-at-isb | requests+BS4 | `.vacancies` |
| iscs | ISCS Central Switzerland | A | direct_email | info@iscs.ch | A | iscs.ch | requests+BS4 | TBD setup |
| beau | Collège Alpin Beau Soleil | A | portal_nord_anglia | Director General | B | nordangliaeducation.com | Playwright | TBD setup |
| vis | Verbier International School | A | direct_email_director | grant.ferguson@vischool.ch | B | verbierinternationalschool.ch | requests+BS4 | TBD setup |
| ges | Geneva English School | B | direct_email | admin@genevaeenglishschool.ch | A | genevaeenglishschool.ch | requests+BS4 | TBD setup |
| lgr | La Garenne International | B | direct_email | info@lagarenne.ch | B | lagarenne.ch | requests+BS4 | TBD setup |
| hau | Haut Lac Bilingual School | B | direct_email | info@hautlac.ch | A | hautlac.ch | requests+BS4 | TBD setup |
| lci | La Cote International School | B | portal_nord_anglia | Director | A | nordangliaeducation.com | Playwright | TBD setup |
| chp | Collège Champittet Nyon | B | portal_nord_anglia | Director campus Nyon | A | nordangliaeducation.com | Playwright | TBD setup |
| riv | Ecole Riviera | B | direct_email | info@ecole-riviera.ch | B | ecole-riviera.ch | requests+BS4 | TBD setup |
| bsb | British School Bern | B | direct_email_director | barbara.bush@britishschool.ch | B | britishschool.ch | requests+BS4 | TBD setup |
| mos | Mosaic Ecole | B | TBD verify | TBD | A | TBD | requests+BS4 | TBD setup |
| wte | Wisdom Tree Education | B | TBD verify | TBD | A | TBD | requests+BS4 | TBD setup |
| isr | ISR International School Rheintal | C | direct_email | info@isr.ch | A | isr.ch | requests+BS4 | TBD setup |
| gri | Colegio Grindelwald | C | TBD verify | TBD | B | TBD | TBD | TBD setup |

---

## Estrategia técnica de scraping por tipo de página

### Páginas estáticas simples
**Colegios:** British School Bern, Ecole Riviera, La Garenne, VIS, ISR, Haut Lac, LCIS, Geneva English School

```python
import requests
from bs4 import BeautifulSoup
import hashlib

def scrape_static(url: str, selector: str) -> dict:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JobMonitor/1.0)"}
    response = requests.get(url, headers=headers, timeout=15)
    soup = BeautifulSoup(response.text, "html.parser")
    block = soup.select_one(selector)
    content = block.get_text(strip=True) if block else ""
    return {
        "content": content,
        "hash": hashlib.sha256(content.encode()).hexdigest(),
        "raw_html": str(block)
    }
```

Mínimo **8–12 segundos entre requests al mismo dominio.**

### Páginas JS-rendered
**Colegios / portales:** Nord Anglia, SchoolSpring, Ecolint, posiblemente ZIS

```python
from playwright.async_api import async_playwright

async def scrape_dynamic(url: str, selector: str) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_selector(selector, timeout=10000)
        block = await page.query_selector(selector)
        content = await block.inner_text() if block else ""
        html = await block.inner_html() if block else ""
        await browser.close()
        return {
            "content": content,
            "hash": hashlib.sha256(content.encode()).hexdigest(),
            "raw_html": html
        }
```

### RSS feeds
**Fuentes:** TES Jobs (principal), posiblemente COBIS, LinkedIn Jobs búsquedas guardadas

```python
import feedparser

TES_RSS = "https://www.tes.com/jobs/search/rss?q=IT+technology&location=Switzerland"

def poll_rss(feed_url: str) -> list[dict]:
    feed = feedparser.parse(feed_url)
    return [
        {
            "title":   entry.title,
            "url":     entry.link,
            "summary": entry.summary[:500],
            "date":    entry.published,
            "source":  "TES"
        }
        for entry in feed.entries
    ]
```

### LinkedIn Jobs (fallback sin API)
LinkedIn deshabilita periódicamente el RSS de búsquedas guardadas. Alternativa práctica:
1. RSS de búsqueda guardada (mientras funcione): `linkedin.com/jobs/search/rss?keywords=IT+Manager+international+school&location=Switzerland`
2. Si el RSS falla: revisión manual diaria de 5 minutos.
3. No construir dependencia crítica en scraping autenticado de LinkedIn — viola los ToS.

---

## Consideraciones técnicas críticas

### robots.txt y rate limiting
- Respetar siempre `robots.txt`.
- Mínimo 8–12 segundos entre requests al mismo dominio.
- Exponential backoff ante errores 429 o 503: esperar 60s → 120s → 240s.
- Objetivo: ser invisible, no agresivo.

### Hash comparison correcto — evitar falsos positivos
- **No** hashear toda la página (menús, banners, fechas cambian constantemente).
- Hashear **solo el bloque de vacantes** identificado por selector CSS.
- Identificar el selector correcto durante el setup inicial de cada colegio.
- Si el selector desaparece (rediseño del site) → alerta de error del módulo, no falsa vacante.

### Deduplicación entre fuentes
La misma vacante puede aparecer en TES, web del colegio, LinkedIn y jobs.ch simultáneamente.

```python
def dedup_key(school_id: str, title: str, date_detected: str) -> str:
    normalized_title = title.lower().strip()
    week = date_detected[:7]  # YYYY-MM — ventana de 1 mes
    return f"{school_id}::{normalized_title}::{week}"
```

Una oferta = una entrada en la DB. No lanzar más de una alerta por la misma oferta.

### Calendario escolar awareness

| Período | Descripción | Frecuencia scraping |
|---|---|---|
| Enero–abril | 🔴 Pico principal (contratos para septiembre) | Máxima (3×/día Grupo A) |
| Mayo–junio | 🟠 Final de ciclo, último mes activo | Alta (2×/día) |
| Julio–agosto | ⚫ Cierre — prácticamente sin vacantes IT | Mínima (1×/día) |
| Septiembre–noviembre | 🟠 Pico secundario (contratos para enero) | Alta (2×/día) |
| Diciembre | 🟡 Pausa fin de año | Normal (1×/día) |

### Monitorización del propio módulo
El módulo debe notificarte también cuando **falla**. Si el scraper de Ecolint lleva 24 horas sin ejecutarse correctamente porque la estructura HTML cambió, necesitas saberlo de inmediato.

Implementar un healthcheck que envíe diariamente: `"Módulo activo — N ofertas detectadas hoy — N errores de scraping"`.

### Retención histórica
- Guardar snapshots HTML durante mínimo **90 días**.
- Sirven para: comparar diffs en cambios parciales, reconstruir texto de ofertas desaparecidas antes de leerlas, y analizar patrones estacionales por colegio.

---

## Condiciones para que el módulo sea realmente funcional

Un scraper genérico de empleo falla en los puntos donde este caso es especial. El módulo necesita estas cuatro propiedades adicionales:

**1. Conciencia de política por colegio.**
ZIS y Ecolint no aceptan candidaturas espontáneas — una alerta de esos colegios requiere una acción diferente (abrir el portal) que una alerta de ISB (enviar email directo). La tabla de metadatos no es opcional — es la pieza que hace el módulo inteligente.

**2. Conciencia de calendario escolar.**
Las ventanas de contratación son enero–abril y octubre–noviembre. Una alerta en junio tiene urgencia temporal completamente diferente a la misma alerta en enero. El módulo debe duplicar la frecuencia en ventanas pico y reducirla fuera de ellas.

**3. Clasificador que filtra roles docentes IT.**
Los colegios publican frecuentemente posiciones de "IT teacher" o "Computer Science teacher" — roles docentes con "IT" en el título pero que no son el objetivo. La exclusión explícita de "teacher" y "professor" en los títulos es tan importante como el match positivo.

**4. Deduplicación entre fuentes.**
Sin deduplicación, el módulo lanza cuatro alertas para la misma oportunidad (web + TES + LinkedIn + jobs.ch), genera fatiga de notificación y la tabla de tracking queda con entradas duplicadas.

---

## Dos canales de notificación para evitar fatiga

| Canal | Umbral | Cuándo |
|---|---|---|
| Push inmediato | score ≥ 70 | Alta confianza — actuar en horas |
| Email digest diario | score 40–69 | Posibles — revisar al final del día |
| Descarte silencioso + log | score < 40 | Irrelevante — guardado pero sin notificar |

Ajustar los umbrales tras las primeras semanas de uso real.

---

## Una cosa que no debes automatizar

El contenido personalizado de la carta. La detección, clasificación, borrador con placeholders, calendario y log son todos automatizables. Pero el párrafo de gancho específico del colegio (el que referencia algo concreto del centro) requiere revisión humana — son 5 minutos. Si se delega a la automatización, la carta pierde exactamente el elemento que la diferencia del resto.

**Automatizar todo excepto:** el texto del párrafo 3 de la carta (school-specific hook) y la decisión final de envío.

---

*Documento generado: 28 de mayo de 2026*
*Contexto: candidatura IT para colegios británicos e internacionales en Suiza — Vicente Ricardo Pau Valero*
