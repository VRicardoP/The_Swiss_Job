"""Scraper for Financejobs.ch — finance sector jobs in Switzerland.

Uses Next.js __NEXT_DATA__ embedded JSON rather than DOM scraping,
which is more reliable than CSS selectors on styled-components.
"""

import json
import logging
import re

from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper
from utils import fetch_diagnostics as diag
from utils.dates import parse_published_at
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

BASE_URL = "https://www.financejobs.ch"

# Rutas conocidas desde `props` hasta el bloque `jobsSSR` del __NEXT_DATA__.
# Next.js ya cambió esta estructura una vez (VD.7): hasta ~2026-02 colgaba de
# `initialProps`; la sonda en vivo del 2026-08-14 confirma que hoy cuelga
# directamente de `pageProps`. Se prueban en orden y gana la primera que
# exista — así un nuevo cambio de forma no vuelve a manifestarse como
# "0 ofertas" en silencio (ver `_extract_jobs_ssr`).
_JOBS_SSR_PATHS: tuple[tuple[str, ...], ...] = (
    ("pageProps", "jobsSSR"),  # estructura actual (sonda 2026-08-14)
    ("initialProps", "pageProps", "jobsSSR"),  # estructura histórica (2026-02-28)
)


# jobId REAL del portal: entero (fixtures y sonda 2026-08-14/15, p. ej.
# 14697110). Como string solo se acepta decimal ASCII — `[0-9]` y no
# isdigit()/`\d`, que casan dígitos unicode (١٤) que el portal nunca emite
# (misma regla que gastrojob, VD.4b r2).
_DECIMAL_ID_RE = re.compile(r"^[0-9]+$")
# jcJobId: UUID canónico en minúsculas, la forma real del portal
# ("cbbceba0-ab30-4f23-91fc-9fe4cf3bc8a0").
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _job_url_id(job: dict) -> str:
    """Devuelve el id de la oferta validado para interpolar en la URL, o "".

    Se blindaron los campos de texto pero el id se interpolaba tal cual: un
    jobId arbitrario ({'x': 1}, '42?utm=x', '42#frag', '../admin') produce
    URLs distintas para la misma identidad y rompe la estabilidad de la
    deduplicación (hash title|company|url + ix_jobs_url). Lo que no case con
    las formas reales del portal se trata como oferta sin URL utilizable y se
    salta (misma regla que el resto de la fase).
    """
    job_id = job.get("jobId")
    # bool es subclase de int: `true` no es un id y saldría como "True".
    if isinstance(job_id, int) and not isinstance(job_id, bool) and job_id >= 0:
        return str(job_id)
    if isinstance(job_id, str) and _DECIMAL_ID_RE.fullmatch(job_id):
        return job_id
    jc_job_id = job.get("jcJobId")
    if isinstance(jc_job_id, str) and _UUID_RE.fullmatch(jc_job_id):
        return jc_job_id
    return ""


def _s(value: object) -> str:
    """Devuelve `value` solo si es str; cadena vacía en caso contrario.

    Blindaje por-campo: `location: null` es JSON normal (oferta sin ubicación)
    y `job.get("location", "")` devuelve None porque la clave EXISTE — sin este
    guard, el `.strip()` posterior tumbaba la página ENTERA con AttributeError
    y el run se quedaba sin veredicto en source_health. Una oferta con un campo
    de tipo inesperado debe degradar ESA oferta, nunca la página.
    """
    return value if isinstance(value, str) else ""


class FinancejobsScraper(BaseScraper):
    SOURCE_NAME = "financejobs"
    LISTING_URL = f"{BASE_URL}/de/jobs"
    RATE_LIMIT_SECONDS = 2.0
    MAX_PAGES = 10
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False  # __NEXT_DATA__ contains all info
    # Sonda en vivo 2026-08-14: jobsSSR.pageSize = 10 (con 20 el motor daba
    # por terminada la paginación tras la primera página).
    PAGE_SIZE = 10

    def build_listing_url(self, page: int, query: str) -> str:
        return f"{self.LISTING_URL}?page={page}"

    @staticmethod
    def _extract_jobs_ssr(data: dict) -> dict | None:
        """Localiza el bloque `jobsSSR` probando las rutas conocidas de props.

        Devuelve el dict `jobsSSR` de la primera ruta que exista, o None si
        NINGUNA existe (estructura de Next.js desconocida). El llamante debe
        tratar ese None como fallo visible, nunca como "0 ofertas": el bug
        original de VD.7 fue exactamente una ruta obsoleta que devolvía lista
        vacía en silencio.
        """
        for path in _JOBS_SSR_PATHS:
            node: object = data.get("props", {})
            for key in path:
                if not isinstance(node, dict):
                    break
                node = node.get(key)
            if isinstance(node, dict):
                return node
        return None

    def _record_structure_failure(self, detail: str) -> None:
        """Registra un fallo de estructura como error de fetch VISIBLE.

        Un HTTP 200 cuyo contenido no podemos leer NO es "no hay ofertas":
        se anota en fetch_diagnostics para que el veredicto del run sea
        `error` (source_health) y no `empty` en silencio.
        """
        logger.error("financejobs: %s", detail)
        diag.record(diag.KIND_NETWORK, self.LISTING_URL, detail=detail)

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract jobs from __NEXT_DATA__ JSON embedded in the page."""
        script_el = soup.select_one("script#__NEXT_DATA__")
        if not script_el or not script_el.string:
            self._record_structure_failure("no __NEXT_DATA__ found")
            return []

        try:
            data = json.loads(script_el.string)
        except json.JSONDecodeError as e:
            self._record_structure_failure(f"failed to parse __NEXT_DATA__: {e}")
            return []

        # JSON válido pero no-objeto (p. ej. una lista) ⇒ estructura desconocida.
        if not isinstance(data, dict):
            self._record_structure_failure(
                "__NEXT_DATA__ no es un objeto JSON (estructura desconocida)"
            )
            return []

        jobs_ssr = self._extract_jobs_ssr(data)
        if jobs_ssr is None:
            self._record_structure_failure(
                "estructura __NEXT_DATA__ desconocida: ninguna ruta conocida "
                "hacia jobsSSR existe (¿Next.js cambió la forma de props otra vez?)"
            )
            return []

        # En el portal real `jobs` existe siempre (sonda 2026-08-14): su
        # ausencia, un null o un tipo inesperado son estructura desconocida,
        # no "0 ofertas". Solo una lista VACÍA es vacío legítimo (sin fallo).
        jobs_data = jobs_ssr.get("jobs")
        if not isinstance(jobs_data, list):
            self._record_structure_failure(
                "jobsSSR.jobs no es una lista (estructura desconocida)"
            )
            return []

        stubs: list[dict] = []
        for job in jobs_data:
            if not isinstance(job, dict):
                continue

            job_id = _job_url_id(job)
            title = _s(job.get("title"))
            company = _s(job.get("companyName")) or "Unknown"
            location = _s(job.get("location"))
            description = _s(job.get("description")) or _s(job.get("summary"))
            employment_type = _s(job.get("workload"))
            logo = job.get("companyLogo") or job.get("logoImage") or None

            url = f"{BASE_URL}/de/job/{job_id}" if job_id else ""

            if not title or not url:
                continue

            stubs.append(
                {
                    "title": strip_html_tags(title).strip(),
                    "company": company.strip(),
                    "location": location.strip(),
                    "url": url,
                    "description": strip_html_tags(description),
                    "employment_type": employment_type,
                    "salary_original": job.get("salary"),
                    "logo": logo,
                    # Fecha de publicación del portal (ISO8601 en __NEXT_DATA__).
                    "date_posted": job.get("datePosted"),
                }
            )

        # Página NO vacía de la que no sale ni un stub: estructura desconocida
        # (p. ej. el portal renombró `title` y todo cayó en los `continue`),
        # no vacío legítimo — sin este guard el run saldría `empty` con 0
        # issues, el bug original de VD.7 con otra ropa. El vacío legítimo
        # (`jobs: []`) no entra, y con 1 stub válido el run sigue siendo `ok`.
        if jobs_data and not stubs:
            self._record_structure_failure(
                f"jobsSSR.jobs trae {len(jobs_data)} elementos y ninguno es parseable"
            )

        return stubs

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        """Not used — FETCH_DETAILS is False."""
        return {}

    def normalize_job(self, raw: dict) -> dict:
        title = raw.get("title", "").strip()
        company = raw.get("company", "Unknown").strip()
        url = raw.get("url", "").strip()
        description = raw.get("description", "")
        location = raw.get("location", "Switzerland").strip()

        tags = extract_job_skills(title, description)

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location,
            "canton": extract_canton(location),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": False,
            "tags": tags[: self.MAX_TAGS],
            "logo": raw.get("logo"),
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": raw.get("salary_original"),
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": raw.get("employment_type"),
            "published_at": parse_published_at(raw.get("date_posted")),
        }
