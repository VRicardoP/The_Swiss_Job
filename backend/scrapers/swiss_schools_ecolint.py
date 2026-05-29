"""Scraper para International School of Geneva (Ecolint).

Estrategia:
- Página /en/job-opportunities (Drupal nativo)
- Cada vacante: article.node-job-teaser con <a href="/en/about/careers/<slug>">
  + <span class="field f-n-title"> que tiene el título
- Paginación: ?page=N (Drupal-style, 0-indexed)
"""

import logging

from bs4 import BeautifulSoup, Tag

from scrapers.swiss_schools_config import get_school
from services.scraper_engine import BaseScraper

logger = logging.getLogger(__name__)

ECOLINT_BASE = "https://www.ecolint.ch"
ECOLINT_LISTING = f"{ECOLINT_BASE}/en/job-opportunities"


class SwissSchoolsEcolintScraper(BaseScraper):
    SOURCE_NAME = "swiss_schools_ecolint"
    LISTING_URL = ECOLINT_LISTING
    RATE_LIMIT_SECONDS = 3.0
    MAX_PAGES = 5
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False
    PAGE_SIZE = 6  # Ecolint muestra 6 por página

    def __init__(self):
        super().__init__()
        self._school = get_school("ecolint_geneva")

    def build_listing_url(self, page: int, query: str) -> str:
        # Drupal usa page=0 para la primera
        return f"{ECOLINT_LISTING}?page={page - 1}"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        if not self._school:
            return []

        out: list[dict] = []
        for art in soup.select("article.node-job-teaser, article.node-job"):
            stub = self._parse_article(art)
            if stub:
                out.append(stub)
        return out

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        return {}

    def normalize_job(self, raw: dict) -> dict:
        raw["hash"] = self.compute_hash(raw["title"], raw["company"], raw["url"])
        return raw

    def _parse_article(self, art: Tag) -> dict | None:
        title_el = art.select_one("span.f-n-title, .field-name-title-field")
        link_el = art.select_one('a[href*="/about/careers/"]')
        if not title_el or not link_el:
            return None

        title = title_el.get_text(strip=True)
        href = link_el.get("href", "")
        if not title or not href:
            return None

        url = href if href.startswith("http") else f"{ECOLINT_BASE}{href}"
        source_id = href.rstrip("/").split("/")[-1] or url

        return {
            "source": self.SOURCE_NAME,
            "source_id": source_id,
            "title": title,
            "company": self._school.name,
            "location": f"{self._school.city}, CH",
            "url": url,
            "tags": [
                "education",
                "international school",
                self._school.id,
            ],
            "language": "en",
        }
