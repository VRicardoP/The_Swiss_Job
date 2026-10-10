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
        return self.LISTING_URL

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
        # Forma histórica: h3 con fondo destacado. La plantilla vigente usa
        # una tarjeta gris y pone el título en el primer párrafo grande.
        title_el = widget.select_one("h3 span[style*='background-color']")
        if title_el is None:
            card = widget.select_one("div[style*='background-color'][style*='padding']")
            title_el = card.select_one("p[style*='font-size'] strong") if card else None
        if title_el is None:
            return None

        title = title_el.get_text(strip=True)
        if not title or len(title) < 5:
            return None
        if title.lower() in {"profile", "profil", "mission", "responsibilities"}:
            return None
        source_id = title.lower().replace(" ", "-")[:80]
        return {
            "source": self.SOURCE_NAME,
            "source_id": source_id,
            "title": title,
            "company": self._school.name,
            "location": f"{self._school.city}, CH",
            # G1/P1-2: URL sintética ESTABLE por vacante. `jobs.url` es
            # UNIQUE y el upsert resuelve conflicto solo por hash: con la
            # URL del listado compartida, la segunda vacante simultánea
            # abortaba el savepoint y una vacante nueva quedaba invisible
            # hasta la purga (60 días) de la que sustituía (clase VD.1).
            "url": f"{HAUTLAC_URL}#{source_id}",
            "tags": ["education", "international school", self._school.id],
            "language": "en",  # mezcla EN/FR; el clasificador lo refina
        }
