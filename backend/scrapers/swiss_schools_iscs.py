"""Scraper para International School of Central Switzerland (ISCS).

Estrategia:
- Página /employment/ (HTML estático).
- Las vacantes están en <li> que contienen <strong> con TÍTULO en mayúsculas
  y palabras clave TEACHER/COORDINATOR/PROGRAMME.
- Se aplica filtro de longitud (<200 chars) y keywords para evitar falsos
  positivos (los <li> de navegación son cortos sin esas keywords).
"""

import logging

from bs4 import BeautifulSoup, Tag

from scrapers.swiss_schools_config import get_school
from services.scraper_engine import BaseScraper

logger = logging.getLogger(__name__)

ISCS_URL = "https://iscs-zug.ch/employment/"

_JOB_KEYWORDS = (
    "TEACHER",
    "COORDINATOR",
    "PROGRAMME",
    "PROGRAM",
    "ACADEMY",
    "DIRECTOR",
    "PRINCIPAL",
    "ASSISTANT",
)


class SwissSchoolsISCSScraper(BaseScraper):
    SOURCE_NAME = "swiss_schools_iscs"
    LISTING_URL = ISCS_URL
    RATE_LIMIT_SECONDS = 3.0
    MAX_PAGES = 1
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False
    PAGE_SIZE = 30

    def __init__(self):
        super().__init__()
        self._school = get_school("iscs_zug")

    def build_listing_url(self, page: int, query: str) -> str:
        return ISCS_URL

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        if not self._school:
            return []

        out: list[dict] = []
        seen: set[str] = set()

        for li in soup.select("li"):
            stub = self._parse_li(li)
            if not stub:
                continue
            key = stub["title"].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(stub)
        return out

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        return {}

    def normalize_job(self, raw: dict) -> dict:
        raw["hash"] = self.compute_hash(raw["title"], raw["company"], raw["url"])
        return raw

    def _parse_li(self, li: Tag) -> dict | None:
        text = li.get_text(" ", strip=True)
        if len(text) > 200:
            return None
        upper = text.upper()
        if not any(kw in upper for kw in _JOB_KEYWORDS):
            return None
        # Hace falta que contenga al menos un <strong> para excluir navegación
        strong = li.select_one("strong, b")
        if not strong:
            return None
        title = strong.get_text(" ", strip=True)
        if len(title) < 5:
            return None

        return {
            "source": self.SOURCE_NAME,
            "source_id": title.lower().replace(" ", "-")[:80],
            "title": title.title(),  # Normalizar mayúsculas a Title Case
            "company": self._school.name,
            "location": f"{self._school.city}, CH",
            "url": ISCS_URL,
            "tags": ["education", "international school", self._school.id],
            "language": "en",
        }
