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
from typing import Literal

# Política de candidatura — describe CÓMO se postula a este colegio.
# La capa de matching no la usa; sirve a la "action layer" del módulo.
Policy = Literal[
    "direct_email_ok",          # acepta candidaturas espontáneas por email HR
    "portal_only",              # solo vía portal/formulario; no email abierto
    "portal_nord_anglia",       # canaliza todo por careers.nordangliaeducation.com
    "portal_workday",           # ATS Workday del grupo (ISP)
    "portal_successfactors",    # SuccessFactors del grupo Inspired
    "manual",                   # requiere contacto humano caso a caso
]

# Plantilla de carta recomendada (la rellena el doc generator).
# Texto definitivo lo aportará el usuario más adelante.
TemplateId = Literal["A", "B"]

# Tier de prioridad para la watchlist (volumen + renombre).
GroupTier = Literal["A", "B", "C"]


@dataclass(frozen=True)
class WatchedSchool:
    id: str                            # Identificador interno (estable, slug)
    name: str                          # Nombre legible
    city: str                          # Ciudad para metadatos
    careers_url: str                   # URL a la página de carreras
    strategy: str                      # Una de las estrategias soportadas
    params: dict | None = None         # Parámetros específicos de la estrategia

    # ── Action-layer metadata (Fase 2, investigación humana) ──────────────
    group_tier: GroupTier = "B"        # Prioridad: A=grandes/renombrados, C=pequeños
    policy: Policy = "manual"          # Cómo se postula
    contact_email: str | None = None   # HR/recruitment público (None si no hay)
    contact_name: str | None = None    # Nombre del HR/Director si conocido
    template_id: TemplateId = "A"      # A=urbano/formal, B=boarding/pequeño/cálido
    application_url: str | None = None # URL específica para spontaneous si difiere
    notes: str | None = None           # Particularidades del proceso


SCHOOLS: list[WatchedSchool] = [
    # ── Nord Anglia Education (3 colegios, 1 scraper) ──────────────────────
    WatchedSchool(
        id="champittet_nyon",
        name="Collège Champittet",
        city="Nyon",
        careers_url="https://careers.nordangliaeducation.com/search/?q=champittet",
        strategy="nae_central",
        params={"keyword": "champittet"},
        group_tier="A",
        policy="portal_nord_anglia",
        contact_email="nyon@champittet.ch",
        template_id="A",
        application_url="https://careers.nordangliaeducation.com/job/Lausanne-Share-Your-Profile-With-Coll%C3%A8ge-Champittet!/1025203301/",
        notes="Nord Anglia. 'Share Your Profile' acepta spontaneous. Campus Pully+Nyon.",
    ),
    WatchedSchool(
        id="beausoleil_villars",
        name="Collège Alpin Beau Soleil",
        city="Villars-sur-Ollon",
        careers_url="https://careers.nordangliaeducation.com/search/?q=beau+soleil",
        strategy="nae_central",
        params={"keyword": "beau soleil"},
        group_tier="A",
        policy="portal_nord_anglia",
        contact_email=None,
        template_id="B",
        application_url="https://careers.nordangliaeducation.com/job/Villars-sur-Ollon-Share-Your-Profile-With-Coll%C3%A8ge-Beau-Soleil!/1025207001/",
        notes="Boarding alpino prestigioso. 'Share Your Profile' para spontaneous.",
    ),
    WatchedSchool(
        id="lcis_aubonne",
        name="La Côte International School Aubonne",
        city="Aubonne",
        careers_url="https://careers.nordangliaeducation.com/search/?q=la+cote",
        strategy="nae_central",
        params={"keyword": "la cote"},
        group_tier="A",
        policy="portal_nord_anglia",
        contact_email=None,
        template_id="A",
        application_url="https://careers.nordangliaeducation.com/job/Aubonne-Share-Your-Profile-With-La-C%C3%B4te-International-School-Aubonne!/1025207901/",
        notes="Nord Anglia. 400+ alumnos. 'Share Your Profile' para spontaneous.",
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
            "school_filter": "mosaic",
        },
        group_tier="C",
        policy="portal_workday",
        contact_email="info@ecolemosaic.ch",
        template_id="B",
        application_url="https://internationalschools.wd3.myworkdayjobs.com/ISPCareers",
        notes="Pertenece a ISP. Email general acepta CV. Rango 3-13 años.",
    ),

    # ── Inspired Education / SuccessFactors (2 colegios) ───────────────────
    WatchedSchool(
        id="ges_versoix",
        name="Geneva English School",
        city="Versoix",
        careers_url="https://jobs.inspirededu.com/search/?q=Geneva+English",
        strategy="inspired_sf",
        params={"keyword": "geneva english"},
        group_tier="B",
        policy="portal_successfactors",
        contact_email=None,
        template_id="A",
        application_url="https://jobs.inspirededu.com/",
        notes="Inspired Education. ~350 alumnos. Anuncia también en TES.",
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
        group_tier="A",
        policy="portal_successfactors",
        contact_email=None,
        template_id="B",
        application_url="https://jobs.inspirededu.com/job/Montreux-Career-Opportunities-Spontaneous-Application/799096002/",
        notes="Inspired. ~400 alumnos boarding+day. Spontaneous expreso en portal.",
    ),

    # ── SchoolSpring (1 colegio) ───────────────────────────────────────────
    WatchedSchool(
        id="zis_zurich",
        name="Zurich International School",
        city="Zurich",
        careers_url="https://zurichinternational.schoolspring.com/",
        strategy="schoolspring",
        params={"subdomain": "zurichinternational"},
        group_tier="A",
        policy="portal_only",
        contact_email="applications@zis.ch",
        contact_name="Elsa Hernández-Donohue",
        template_id="A",
        application_url="https://www.zis.ch/one-zis-community/employment",
        notes="Pide aplicar via portal, NO email/post. 1250+ alumnos.",
    ),

    # ── AbaServices (1 colegio) ────────────────────────────────────────────
    WatchedSchool(
        id="isr_buchs",
        name="International School Rheintal",
        city="Buchs",
        careers_url="https://www.isr.ch/our-school/working-with-us",
        strategy="abaservices",
        params={"portal_url": "https://app.jobportal.abaservices.ch"},
        group_tier="B",
        policy="portal_only",
        contact_email="hr@isr.ch",
        contact_name="Rene Sprecher",
        template_id="B",
        application_url="https://app.jobportal.abaservices.ch",
        notes="Form obligatorio (AbaServices). NO acepta CV solo. Top-10 IB CH.",
    ),

    # ── Finalsite (1 colegio) ──────────────────────────────────────────────
    WatchedSchool(
        id="isb_basel",
        name="International School Basel",
        city="Basel",
        careers_url="https://www.isbasel.ch/connect/news/?board=employment-public-job-postings",
        strategy="finalsite_board",
        params={"board": "employment-public-job-postings"},
        group_tier="A",
        policy="direct_email_ok",
        contact_email="recruitment@isbasel.ch",
        template_id="A",
        application_url="https://www.isbasel.ch/join/working-at-isb",
        notes="Acepta CV año redondo para substitute/tutor. Prioridad CH/EU permit.",
    ),

    # ── HubSpot CMS (1 colegio) ────────────────────────────────────────────
    WatchedSchool(
        id="hautlac_stlegier",
        name="Haut-Lac Bilingual School",
        city="Saint-Légier-La Chiésaz",
        careers_url="https://info.haut-lac.ch/jobs-and-career",
        strategy="hubspot_cms",
        params=None,
        group_tier="B",
        policy="direct_email_ok",
        contact_email="jobs@haut-lac.ch",
        template_id="B",
        application_url="https://info.haut-lac.ch/jobs-and-career",
        notes="~600 alumnos. Pide CV+diplomas+experiencia+referencias.",
    ),

    # ── Drupal nativo (1 colegio) ──────────────────────────────────────────
    WatchedSchool(
        id="ecolint_geneva",
        name="International School of Geneva (Ecolint)",
        city="Geneva",
        careers_url="https://www.ecolint.ch/en/job-opportunities",
        strategy="drupal_ecolint",
        params=None,
        group_tier="A",
        policy="portal_only",
        contact_email=None,
        contact_name="Soizic Le Clère",
        template_id="A",
        application_url="https://www.ecolint.ch/en/job-opportunities",
        notes="4500 alumnos, 1250 staff, 3 campus. SOLO online: cover+CV+3 refs.",
    ),

    # ── HTML estático propio (4 colegios) ──────────────────────────────────
    WatchedSchool(
        id="riviera_montreux",
        name="Ecole Riviera",
        city="Montreux",
        careers_url="https://ecole-riviera.ch/fr/careers-2/",
        strategy="html_css",
        params=None,
        group_tier="C",
        policy="direct_email_ok",
        contact_email="info@ecole-riviera.ch",
        template_id="B",
        application_url="https://ecole-riviera.ch/fr/careers-2/",
        notes="Pequeña bilingüe FR/EN, 3 meses-12 años.",
    ),
    WatchedSchool(
        id="iscs_zug",
        name="ISCS — International School of Central Switzerland",
        city="Cham/Zug",
        careers_url="https://iscs-zug.ch/employment/",
        strategy="html_css",
        params=None,
        group_tier="B",
        policy="direct_email_ok",
        contact_email="recruitment@iscs-zug.ch",
        template_id="B",
        application_url="https://iscs-zug.ch/employment/",
        notes="Cambridge. PDF único cover+CV+3 refs. Solo permit CH válido.",
    ),
    WatchedSchool(
        id="verbier_vis",
        name="Verbier International School",
        city="Verbier",
        careers_url="https://verbierinternationalschool.ch/careers/",
        strategy="html_css",
        params=None,
        group_tier="C",
        policy="direct_email_ok",
        contact_email="info@vischool.ch",
        template_id="B",
        application_url="https://verbierinternationalschool.ch/careers/",
        notes="Pequeña alpina. Acepta spontaneous SIN vacante. FR o EN.",
    ),
    WatchedSchool(
        id="beausoleil_inline",
        name="Collège Alpin Beau Soleil (sitio propio)",
        city="Villars-sur-Ollon",
        careers_url="https://www.beausoleil.ch/careers",
        strategy="html_css",
        params=None,
        group_tier="A",
        policy="portal_nord_anglia",
        contact_email=None,
        template_id="B",
        application_url="https://careers.nordangliaeducation.com/job/Villars-sur-Ollon-Share-Your-Profile-With-Coll%C3%A8ge-Beau-Soleil!/1025207001/",
        notes="Mirror del Nord Anglia central. Algunas vacantes inline en propia web.",
    ),

    # ── Manual / sin listado público (3 colegios) ──────────────────────────
    WatchedSchool(
        id="bsb_bern",
        name="British School Bern",
        city="Bern",
        careers_url="https://britishschool.ch/employment/",
        strategy="manual",
        params=None,
        group_tier="C",
        policy="direct_email_director",
        contact_email=None,
        template_id="B",
        application_url="https://britishschool.ch/",
        notes="Sin email HR público. Locally owned. Anuncian via Facebook. Form web.",
    ),
    WatchedSchool(
        id="lagarenne_villars",
        name="La Garenne International School",
        city="Villars-sur-Ollon",
        careers_url="https://www.la-garenne.ch/job-vacancies/",
        strategy="manual",
        params={"reason": "anti-bot 403 con UA simple"},
        group_tier="C",
        policy="direct_email_ok",
        contact_email="hr@la-garenne.ch",
        template_id="B",
        application_url="https://www.la-garenne.ch/job-vacancies/",
        notes="Boarding alpino, ~180 alumnos. Background check Swiss obligatorio.",
    ),
    WatchedSchool(
        id="wisdomtree_pully",
        name="Wisdom Tree Education",
        city="Pully",
        careers_url="https://wisdomtreeeducation.com/",
        strategy="manual",
        params={"reason": "publica solo en jobs.ch/jobup.ch (prohibidos)"},
        group_tier="C",
        policy="manual",
        contact_email=None,
        contact_name="Eamon Sharkawi",
        template_id="B",
        application_url="https://wisdomtreeeducation.com/en/contact/",
        notes="Homeschooling pequeño, fundado 2023. Sin HR. Contacto via form web.",
    ),
]


def schools_by_strategy(strategy: str) -> list[WatchedSchool]:
    """Devuelve los colegios que usan la estrategia indicada."""
    return [s for s in SCHOOLS if s.strategy == strategy]


def get_school(school_id: str) -> WatchedSchool | None:
    return next((s for s in SCHOOLS if s.id == school_id), None)


def resolve_school_from_job(job) -> WatchedSchool | None:
    """Identifica el WatchedSchool al que pertenece un job a partir de sus
    tags. Los scrapers de la watchlist insertan school.id como tag.

    Función centralizada para que watchlist router, urgency scorer y
    cualquier futuro consumidor compartan la misma lógica.
    """
    tags = getattr(job, "tags", None)
    if not tags:
        return None
    for tag in tags:
        s = get_school(tag)
        if s is not None:
            return s
    return None
