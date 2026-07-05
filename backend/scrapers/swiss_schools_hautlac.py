"""Scraper para Haut-Lac Bilingual School.

Estrategia:
- Página info.haut-lac.ch/jobs-and-career (HubSpot CMS).
- Cada vacante es un widget hs_cos_wrapper con un <h3> que contiene el
  título dentro de un <span> de color destacado (#0b7992 sobre blanco).
- HTML estático, sin JS.
"""

import logging

from bs4 import BeautifulSoup, Tag

from scrapers.swiss_schools_config import get_school
from scrapers.swiss_schools_base import SwissSchoolBaseScraper

logger = logging.getLogger(__name__)

HAUTLAC_URL = "https://info.haut-lac.ch/jobs-and-career"


class SwissSchoolsHautLacScraper(SwissSchoolBaseScraper):
    SOURCE_NAME = "swiss_schools_hautlac"
    LISTING_URL = HAUTLAC_URL
    RATE_LIMIT_SECONDS = 3.0
    MAX_PAGES = 1
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False
    PAGE_SIZE = 50

    def __init__(self):
        super().__init__()
        self._school = get_school("hautlac_stlegier")

    def build_listing_url(self, page: int, query: str) -> str:
        return HAUTLAC_URL

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        if not self._school:
            return []

        out: list[dict] = []
        seen: set[str] = set()

        # Cada job es un widget. El título está en el primer h3 destacado.
        for widget in soup.select(".hs_cos_wrapper_type_rich_text"):
            stub = self._parse_widget(widget)
            if not stub:
                continue
            if stub["title"] in seen:
                continue
            seen.add(stub["title"])
            out.append(stub)
        return out

    def _parse_widget(self, widget: Tag) -> dict | None:
        # El título es el primer h3 con un span coloreado destacado
        for h3 in widget.select("h3"):
            span = h3.select_one("span[style*='background-color']")
            if not span:
                continue
            title = span.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            # Filtrar h3 que son subapartados ("Profile", "Mission", etc.)
            if title.lower() in {"profile", "profil", "mission", "responsibilities"}:
                continue
            return {
                "source": self.SOURCE_NAME,
                "source_id": title.lower().replace(" ", "-")[:80],
                "title": title,
                "company": self._school.name,
                "location": f"{self._school.city}, CH",
                "url": HAUTLAC_URL,
                "tags": ["education", "international school", self._school.id],
                "language": "en",  # mezcla EN/FR; el clasificador lo refina
            }
        return None
