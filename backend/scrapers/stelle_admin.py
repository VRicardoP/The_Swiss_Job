"""Scraper for jobs.admin.ch (stelle.admin.ch) — Swiss federal government jobs.

Pure JS SPA — requires Playwright headless browser for rendering.
"""

import logging
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper
from utils.text import extract_canton, extract_job_skills

logger = logging.getLogger(__name__)

BASE_URL = "https://jobs.admin.ch"


def _has_own_path(url: str) -> bool:
    """True si la URL apunta a una página PROPIA de la oferta.

    Un path vacío o "/" es la portada o el listado (solo cambia la query), no
    una oferta: todas las ofertas sin `<a href>` salían con la URL base y
    colisionaban entre sí contra `ix_jobs_url` (VD.1).
    """
    return urlsplit(url).path.strip("/") != ""


def _resolve_job_url(href: str) -> str | None:
    """Resuelve un href del DOM a la URL absoluta de la oferta, o None si no
    es utilizable como tal.

    Las validaciones van JUNTAS a propósito: `urljoin` corrige los relativos
    sin `/` inicial (la concatenación producía hosts corruptos tipo
    `jobs.admin.choffene-stellen`), pero también resuelve los `//evil.com`
    protocolo-relativos que aquella neutralizaba por accidente — sin exigir el
    host `admin.ch`, un href ajeno inyectado en el DOM renderizado acabaría
    persistido como URL clicable para el usuario (host confusion / phishing).
    """
    if not href:
        return None
    # Diferencial de parsers urllib/WHATWG (nota de seguridad de urllib.parse):
    # urllib solo corta el netloc en `/ ? #`; los navegadores también en `\`.
    # Con `https://evil.com\@jobs.admin.ch/...` urllib ve un netloc que
    # termina en `.admin.ch`, pero el navegador del usuario navega a
    # `evil.com`. Se rechaza el `\` ANTES de parsear: ningún href legítimo
    # de jobs.admin.ch lo contiene.
    if "\\" in href:
        return None
    try:
        url = urljoin(BASE_URL, href)
        parts = urlsplit(url)
    except ValueError:
        # p. ej. "https://[evil/..." (IPv6 inválido): urljoin/urlsplit LANZAN
        # en vez de parsear (mismo defecto latente que zebis, VD.9/H4). Sin
        # capturarlo, un href malformado en el DOM tumbaba el lote entero.
        return None
    # Solo http(s): otros esquemas con autoridad (`ftp://x.admin.ch/...`)
    # pasarían la comprobación de host sin ser una oferta navegable.
    if parts.scheme not in ("http", "https"):
        return None
    # Sin userinfo: `https://lo-que-sea@sub.admin.ch/x` resuelve a un host
    # seguro, pero permite spoofing visual del enlace que ve el usuario.
    # `username` es "" (no None) también con userinfo vacío (`https://@...`).
    if parts.username is not None:
        return None
    # `.hostname` (no `.netloc`): minusculiza y quita el puerto. OJO: por sí
    # solo NO sustituye al rechazo del `\` — `hostname` parte por la `@` y
    # devolvería `jobs.admin.ch` para el href del ataque.
    host = parts.hostname or ""
    if host != "jobs.admin.ch" and not host.endswith(".admin.ch"):
        return None
    if not _has_own_path(url):
        return None
    return url


class StelleAdminScraper(BaseScraper):
    SOURCE_NAME = "stelle_admin"
    LISTING_URL = BASE_URL
    RATE_LIMIT_SECONDS = 3.0
    MAX_PAGES = 5
    NEEDS_PLAYWRIGHT = True
    FETCH_DETAILS = False  # Extract from listing page DOM after JS render
    PAGE_SIZE = 20

    def build_listing_url(self, page: int, query: str) -> str:
        return f"{BASE_URL}/?lang=de&page={page}"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract job cards from JS-rendered DOM.

        After Playwright renders the SPA, the HTML should contain
        job listing elements. Common patterns for government portals:
        .job-card, .vacancy-item, [role="listitem"], .search-result
        """
        stubs: list[dict] = []

        # Strategy 1: structured job cards
        selectors = [
            ".job-card",
            ".vacancy-item",
            ".search-result-item",
            ".job-list-item",
            "[data-job-id]",
            "article",
        ]

        records = []
        for sel in selectors:
            records = soup.select(sel)
            if records:
                break

        # Strategy 2: table rows (common in government portals)
        if not records:
            records = soup.select("table tbody tr")

        # Dedup por URL absoluta dentro del run: si dos records emiten la
        # misma URL (p. ej. un enlace de paginación con path como primer <a>
        # de cada card), solo el primero cuenta — defensa directa contra la
        # colisión múltiple en ix_jobs_url (VD.1). Nota: la estrategia 3 solo
        # corre si las estrategias 1/2 no emitieron nada, y en ese caso este
        # set llega vacío (cada `add` de 1/2 va seguido de un `append`) — el
        # dedup opera DENTRO de cada estrategia, no entre ellas.
        seen_urls: set[str] = set()

        for record in records:
            # Title
            title_el = record.select_one("h2, h3, h4, .title, a")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            # URL — sin URL propia (resuelta, con host admin.ch y path propio)
            # la oferta no es utilizable (misma regla que aplica el proyector
            # del core): se salta el registro en vez de emitirlo corrupto.
            link_el = record.select_one("a[href]")
            href = link_el.get("href", "") if link_el else ""
            url = _resolve_job_url(href)
            if url is None or url in seen_urls:
                continue
            seen_urls.add(url)

            # Company / Department
            dept_el = record.select_one(".department, .organization, .employer, .amt")
            company = (
                dept_el.get_text(strip=True)
                if dept_el
                else "Swiss Federal Administration"
            )

            # Location
            loc_el = record.select_one(".location, .ort, .arbeitsort")
            location = loc_el.get_text(strip=True) if loc_el else ""

            # Description snippet
            desc_el = record.select_one(".description, .teaser, p")
            snippet = desc_el.get_text(strip=True) if desc_el else ""

            # Employment rate
            rate_el = record.select_one(".pensum, .workload, .rate")
            employment_type = rate_el.get_text(strip=True) if rate_el else None

            stubs.append(
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "url": url,
                    "description": snippet,
                    "employment_type": employment_type,
                }
            )

        # Strategy 3: fallback to links with job-like patterns
        if not stubs:
            # Dedup por URL ABSOLUTA ya resuelta: el mismo enlace en forma
            # relativa y absoluta es la misma identidad real (VD.1).
            for link in soup.select("a[href]"):
                href = link.get("href", "")
                text = link.get_text(strip=True)
                full_url = _resolve_job_url(href)
                # El patrón se comprueba sobre la URL RESUELTA, igual que el
                # dedup: un relativo sin '/' inicial (`offene-stellen/x/uuid`)
                # no contiene `/offene-stellen/` en crudo y se descartaba en
                # silencio, mientras las estrategias 1/2 sí lo aceptan.
                if (
                    full_url is not None
                    and text
                    and len(text) > 10
                    and any(
                        p in full_url.lower()
                        for p in [
                            # Forma real de jobs.admin.ch:
                            # /offene-stellen/<slug>/<uuid> (VD.1)
                            "/offene-stellen/",
                            "/job/",
                            "/stelle/",
                            "/vacancy/",
                            "/detail/",
                        ]
                    )
                    and full_url not in seen_urls
                ):
                    seen_urls.add(full_url)
                    stubs.append(
                        {
                            "title": text,
                            "company": "Swiss Federal Administration",
                            "location": "",
                            "url": full_url,
                            "description": "",
                        }
                    )

        return stubs

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        """Not used — FETCH_DETAILS is False for this scraper."""
        return {}

    def normalize_job(self, raw: dict) -> dict:
        title = raw.get("title", "").strip()
        company = raw.get("company", "Swiss Federal Administration").strip()
        url = raw.get("url", "").strip()
        description = raw.get("description", "")
        location = raw.get("location", "Bern").strip()

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
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": raw.get("employment_type"),
        }
