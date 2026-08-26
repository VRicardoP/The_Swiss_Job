"""Scraper para el portal SuccessFactors del grupo Inspired Education.

Cubre 2 colegios de la watchlist:
- Geneva English School (Versoix)
- St. George's International School (Montreux)

Estrategia:
- Inspired usa la plantilla Lumesse/jobs2web (misma base que NAE) en
  jobs.inspirededu.com, pero con nombres de campo diferentes:
  - facility → School name
  - location → "Geneva, CH"
- Filtra por nombre exacto del colegio en facility.
- Categoría fijada a "A" para saltarse la penalización H.
"""

import logging
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag

from scrapers.swiss_schools_config import WatchedSchool, schools_by_strategy
from scrapers.swiss_schools_base import SwissSchoolBaseScraper

logger = logging.getLogger(__name__)

INSPIRED_BASE = "https://jobs.inspirededu.com"

# Mapa keyword → nombre canónico tal como aparece en el campo "facility".
_SCHOOL_CANONICAL: dict[str, str] = {
    "geneva english": "Geneva English School",
    "george": "St. George's International School",
}


class SwissSchoolsInspiredScraper(SwissSchoolBaseScraper):
    SOURCE_NAME = "swiss_schools_inspired"
    LISTING_URL = f"{INSPIRED_BASE}/search/"
    RATE_LIMIT_SECONDS = 2.0
    MAX_PAGES = 1
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False
    PAGE_SIZE = 100

    def __init__(self):
        super().__init__()
        self._schools: list[WatchedSchool] = schools_by_strategy("inspired_sf")
        self._current_school: WatchedSchool | None = None

    async def _scrape_with_httpx(self, query: str) -> list[dict]:
        """Una pasada del flujo base de scraping por cada colegio vigilado.

        Se sobreescribe ESTE punto y no fetch_jobs (VD.4a): así se hereda el
        ciclo de compliance completo del BaseScraper — pre-check, rearme de
        flags del run y la rehabilitación por "vacío verificado". El antiguo
        override de fetch_jobs conservaba `if results:` para rehabilitar, y en
        una watchlist donde 0 vacantes durante meses es lo NORMAL eso dejaba la
        fuente apagada para siempre si caía en el kill-switch.
        """
        all_jobs: list[dict] = []
        had_error = False
        for school in self._schools:
            self._current_school = school
            all_jobs.extend(await super()._scrape_with_httpx(query))
            # G2/P3-1: las N pasadas comparten `_stop_reason` — si el colegio
            # siguiente termina en early-stop ("known_page"), sobreescribía el
            # "error" parcial del anterior y el guard del cursor en
            # scraping_tasks dejaba de verlo. El error de cualquier pasada gana.
            had_error = had_error or self._stop_reason == "error"
            # G1/P3-10: los N colegios comparten HOST — si uno reporta
            # bloqueo, seguir con el resto sumaría N reportes en un solo run
            # y alcanzaria el kill-switch (threshold 3) por un episodio
            # transitorio (hasta 24h de silencio). Un bloqueo corta el run.
            if self._run_block_reported:
                break
        if had_error:
            self._stop_reason = "error"
        return all_jobs

    def build_listing_url(self, page: int, query: str) -> str:
        keyword = (self._current_school.params or {}).get("keyword", "")
        return f"{INSPIRED_BASE}/search/?q={quote_plus(keyword)}"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        school = self._current_school
        if not school:
            return []

        keyword = (school.params or {}).get("keyword", "")
        canonical = _SCHOOL_CANONICAL.get(keyword, school.name)

        out: list[dict] = []
        for tile in soup.select("li.job-tile"):
            stub = self._parse_tile(tile, canonical, school)
            if stub:
                out.append(stub)
        return out

    def _parse_tile(
        self, tile: Tag, canonical_school: str, school: WatchedSchool
    ) -> dict | None:
        facility = self._extract_field(tile, "facility")
        if not facility or canonical_school.lower() not in facility.lower():
            return None

        title_a = tile.select_one("a.jobTitle-link")
        if not title_a:
            return None
        title = title_a.get_text(strip=True)
        href = title_a.get("href", "")
        url = f"{INSPIRED_BASE}{href}" if href.startswith("/") else href

        data_url = tile.get("data-url", "")
        source_id = data_url.rstrip("/").split("/")[-1] if data_url else url

        location = self._extract_field(tile, "location") or f"{school.city}, CH"
        shift_type = self._extract_field(tile, "shifttype")

        job = {
            "source": self.SOURCE_NAME,
            "source_id": source_id,
            "title": title,
            "company": school.name,
            "location": location,
            "url": url,
            # Categoría real la asigna el classifier; bypass en match_service.
            "tags": ["education", "international school", school.id],
            "language": "en",
        }
        if shift_type:
            # SuccessFactors usa "Permanent Contract", "Fixed Term Contract", etc.
            job["employment_type"] = shift_type
        return job

    @staticmethod
    def _extract_field(tile: Tag, field_name: str) -> str | None:
        el = tile.select_one(f'div[id$="-desktop-section-{field_name}-value"]')
        return el.get_text(strip=True) if el else None
