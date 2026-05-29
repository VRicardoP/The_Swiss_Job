"""Configuración de la lista cerrada de colegios suizos vigilados.

Estos colegios reciben tratamiento especial en el matching:
- Sus ofertas se clasifican forzadamente como categoría "A" (objetivo)
  para evitar la penalización H (docencia) general.
- El usuario quiere reactivar la búsqueda de docencia SOLO para esta lista.

Estrategias soportadas:
- nae_central         → portal único de Nord Anglia Education
- isp_workday         → Workday del grupo International Schools Partnership
- inspired_sf         → SuccessFactors del grupo Inspired Education
- schoolspring        → ATS SchoolSpring (subdominio dedicado por colegio)
- abaservices         → ATS AbaServices (ABACUS ERP)
- finalsite_board     → Sección de noticias de Finalsite con board=...
- hubspot_cms         → CMS HubSpot en subdominio info.*
- drupal_ecolint      → Drupal nativo Ecolint
- html_css            → HTML estático genérico con selectores CSS
- manual              → sin scraping; mostrar enlace al usuario
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WatchedSchool:
    id: str                    # Identificador interno (estable, slug)
    name: str                  # Nombre legible
    city: str                  # Ciudad para metadatos
    careers_url: str           # URL a la página de carreras (para "manual" o referencia)
    strategy: str              # Una de las estrategias soportadas
    params: dict | None = None # Parámetros específicos de la estrategia


SCHOOLS: list[WatchedSchool] = [
    # ── Nord Anglia Education (3 colegios, 1 scraper) ──────────────────────
    WatchedSchool(
        id="champittet_nyon",
        name="Collège Champittet",
        city="Nyon",
        careers_url="https://careers.nordangliaeducation.com/search/?q=champittet",
        strategy="nae_central",
        params={"keyword": "champittet"},
    ),
    WatchedSchool(
        id="beausoleil_villars",
        name="Collège Alpin Beau Soleil",
        city="Villars-sur-Ollon",
        careers_url="https://careers.nordangliaeducation.com/search/?q=beau+soleil",
        strategy="nae_central",
        params={"keyword": "beau soleil"},
    ),
    WatchedSchool(
        id="lcis_aubonne",
        name="La Côte International School Aubonne",
        city="Aubonne",
        careers_url="https://careers.nordangliaeducation.com/search/?q=la+cote",
        strategy="nae_central",
        params={"keyword": "la cote"},
    ),

    # ── ISP / Workday (1 colegio) ──────────────────────────────────────────
    WatchedSchool(
        id="mosaic_geneva",
        name="Mosaic Ecole",
        city="Geneva",
        careers_url="https://internationalschools.wd3.myworkdayjobs.com/ISPCareers",
        strategy="isp_workday",
        params={
            "tenant": "internationalschools",
            "site": "ISPCareers",
            "school_filter": "mosaic",  # se filtra por nombre del colegio en location/title
        },
    ),

    # ── Inspired Education / SuccessFactors (2 colegios) ───────────────────
    WatchedSchool(
        id="ges_versoix",
        name="Geneva English School",
        city="Versoix",
        careers_url="https://jobs.inspirededu.com/search/?q=Geneva+English",
        strategy="inspired_sf",
        params={"keyword": "geneva english"},
    ),
    WatchedSchool(
        id="stgeorges_montreux",
        name="St. George's International School",
        city="Montreux",
        careers_url=(
            "https://jobs.inspirededu.com/search/"
            "?optionsFacetsDD_facility=St.+George%27s+International+School"
        ),
        strategy="inspired_sf",
        params={"keyword": "george"},
    ),

    # ── SchoolSpring (1 colegio) ───────────────────────────────────────────
    WatchedSchool(
        id="zis_zurich",
        name="Zurich International School",
        city="Zurich",
        careers_url="https://zurichinternational.schoolspring.com/",
        strategy="schoolspring",
        params={"subdomain": "zurichinternational"},
    ),

    # ── AbaServices (1 colegio) ────────────────────────────────────────────
    WatchedSchool(
        id="isr_buchs",
        name="International School Rheintal",
        city="Buchs",
        careers_url="https://www.isr.ch/our-school/working-with-us",
        strategy="abaservices",
        params={"portal_url": "https://app.jobportal.abaservices.ch"},
    ),

    # ── Finalsite (1 colegio) ──────────────────────────────────────────────
    WatchedSchool(
        id="isb_basel",
        name="International School Basel",
        city="Basel",
        careers_url="https://www.isbasel.ch/connect/news/?board=employment-public-job-postings",
        strategy="finalsite_board",
        params={"board": "employment-public-job-postings"},
    ),

    # ── HubSpot CMS (1 colegio) ────────────────────────────────────────────
    WatchedSchool(
        id="hautlac_stlegier",
        name="Haut-Lac Bilingual School",
        city="Saint-Légier-La Chiésaz",
        careers_url="https://info.haut-lac.ch/jobs-and-career",
        strategy="hubspot_cms",
        params=None,
    ),

    # ── Drupal nativo (1 colegio) ──────────────────────────────────────────
    WatchedSchool(
        id="ecolint_geneva",
        name="International School of Geneva (Ecolint)",
        city="Geneva",
        careers_url="https://www.ecolint.ch/en/job-opportunities",
        strategy="drupal_ecolint",
        params=None,
    ),

    # ── HTML estático propio (4 colegios) ──────────────────────────────────
    WatchedSchool(
        id="riviera_montreux",
        name="Ecole Riviera",
        city="Montreux",
        careers_url="https://ecole-riviera.ch/fr/careers-2/",
        strategy="html_css",
        params=None,
    ),
    WatchedSchool(
        id="iscs_zug",
        name="ISCS — International School of Central Switzerland",
        city="Cham/Zug",
        careers_url="https://iscs-zug.ch/employment/",
        strategy="html_css",
        params=None,
    ),
    WatchedSchool(
        id="verbier_vis",
        name="Verbier International School",
        city="Verbier",
        careers_url="https://verbierinternationalschool.ch/careers/",
        strategy="html_css",
        params=None,
    ),
    WatchedSchool(
        id="beausoleil_inline",
        name="Collège Alpin Beau Soleil (sitio propio)",
        city="Villars-sur-Ollon",
        careers_url="https://www.beausoleil.ch/careers",
        strategy="html_css",
        params=None,
    ),

    # ── Manual / sin listado público (3 colegios) ──────────────────────────
    WatchedSchool(
        id="bsb_bern",
        name="British School Bern",
        city="Bern",
        careers_url="https://britishschool.ch/employment/",
        strategy="manual",
        params=None,
    ),
    WatchedSchool(
        id="lagarenne_villars",
        name="La Garenne International School",
        city="Villars-sur-Ollon",
        careers_url="https://www.la-garenne.ch/job-vacancies/",
        strategy="manual",
        params={"reason": "anti-bot 403 con UA simple"},
    ),
    WatchedSchool(
        id="wisdomtree_pully",
        name="Wisdom Tree Education",
        city="Pully",
        careers_url="https://wisdomtreeeducation.com/",
        strategy="manual",
        params={"reason": "publica solo en jobs.ch/jobup.ch (prohibidos)"},
    ),
]


def schools_by_strategy(strategy: str) -> list[WatchedSchool]:
    """Devuelve los colegios que usan la estrategia indicada."""
    return [s for s in SCHOOLS if s.strategy == strategy]


def get_school(school_id: str) -> WatchedSchool | None:
    return next((s for s in SCHOOLS if s.id == school_id), None)
