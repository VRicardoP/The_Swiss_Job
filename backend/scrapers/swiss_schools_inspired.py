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
from services.scraper_engine import BaseScraper

logger = logging.getLogger(__name__)

INSPIRED_BASE = "https://jobs.inspirededu.com"

# Mapa keyword → nombre canónico tal como aparece en el campo "facility".
_SCHOOL_CANONICAL: dict[str, str] = {
    "geneva english": "Geneva English School",
    "george": "St. George's International School",
}


class SwissSchoolsInspiredScraper(BaseScraper):
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

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        if not await self._pre_check():
            return []

        all_jobs: list[dict] = []
        for school in self._schools:
            self._current_school = school
            jobs = await self._scrape_with_httpx(query)
            all_jobs.extend(jobs)

        results = self._process_raw_jobs(all_jobs)
        if results:
            await self._reset_compliance_blocks()
        return self._finalize_fetch(results)

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

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        return {}

    def normalize_job(self, raw: dict) -> dict:
        raw["hash"] = self.compute_hash(raw["title"], raw["company"], raw["url"])
        return raw

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
            "category": "A",
            "tags": ["education", "international school", school.id],
            "language": "en",
        }
        if shift_type:
            # SuccessFactors usa "Permanent Contract", "Fixed Term Contract", etc.
            job["employment_type"] = shift_type
        return job

    @staticmethod
    def _extract_field(tile: Tag, field_name: str) -> str | None:
        el = tile.select_one(
            f'div[id$="-desktop-section-{field_name}-value"]'
        )
        return el.get_text(strip=True) if el else None
