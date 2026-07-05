"""Scraper para Zurich International School.

Cubre 1 colegio: ZIS (Group A, ~1250 alumnos).

Estrategia:
- La página propia /one-zis-community/employment lista las vacantes
  con enlaces directos a zurichinternational.schoolspring.com/?jobid=N
- Es HTML estático (Finalsite CMS).
- El portal SchoolSpring en sí es SPA con Incapsula → no scrapeable.
"""

import logging
import re

from bs4 import BeautifulSoup, Tag

from scrapers.swiss_schools_config import get_school
from services.scraper_engine import BaseScraper

logger = logging.getLogger(__name__)

ZIS_PAGE = "https://www.zis.ch/one-zis-community/employment"
_JOBID_RE = re.compile(r"jobid=(\d+)")


class SwissSchoolsZISScraper(BaseScraper):
    SOURCE_NAME = "swiss_schools_zis"
    LISTING_URL = ZIS_PAGE
    RATE_LIMIT_SECONDS = 3.0
    MAX_PAGES = 1
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False
    PAGE_SIZE = 100

    def __init__(self):
        super().__init__()
        self._school = get_school("zis_zurich")

    def build_listing_url(self, page: int, query: str) -> str:
        return ZIS_PAGE

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        if not self._school:
            return []

        out: list[dict] = []
        seen: set[str] = set()

        # Cada vacante está en un <a> con href schoolspring.com/?jobid=N
        for a in soup.select('a[href*="schoolspring.com"]'):
            href = a.get("href", "")
            m = _JOBID_RE.search(href)
            if not m:
                continue
            job_id = m.group(1)
            if job_id in seen:
                continue
            seen.add(job_id)

            title = self._clean_title(a)
            if not title:
                continue

            out.append(
                {
                    "source": self.SOURCE_NAME,
                    "source_id": job_id,
                    "title": title,
                    "company": self._school.name,
                    "location": f"{self._school.city}, CH",
                    "url": href,
                    "tags": [
                        "education",
                        "international school",
                        self._school.id,
                    ],
                    "language": "en",
                }
            )
        return out

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        return {}

    def normalize_job(self, raw: dict) -> dict:
        raw["hash"] = self.compute_hash(raw["title"], raw["company"], raw["url"])
        return raw

    @staticmethod
    def _clean_title(a: Tag) -> str:
        """Saca el título del <a> y elimina sufijos como '- part-time'."""
        title = a.get_text(strip=True)
        # Quitar nbsp y limpiar trailing ' - <modificador>'
        title = title.replace("\xa0", " ").strip()
        return title
