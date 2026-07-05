"""Scraper para International School Basel (ISB).

Estrategia:
- Página /connect/news/?board=employment-public-job-postings (Finalsite CMS)
- Los posts se renderizan en .fsPostElement con board id 141,316,317
- HTML estático cuando hay vacantes; vacío con "No post to display." si no.

Selectores Finalsite habituales:
- article.fsArticle / div.fsPost
- a.fsPostLink (lleva al detalle)
- h2.fsPostTitle / .fsArticleTitle
"""

import logging

from bs4 import BeautifulSoup, Tag

from scrapers.swiss_schools_config import get_school
from scrapers.swiss_schools_base import SwissSchoolBaseScraper

logger = logging.getLogger(__name__)

ISB_BASE = "https://www.isbasel.ch"
ISB_BOARD_URL = f"{ISB_BASE}/connect/news/?board=employment-public-job-postings"


class SwissSchoolsISBScraper(SwissSchoolBaseScraper):
    SOURCE_NAME = "swiss_schools_isb"
    LISTING_URL = ISB_BOARD_URL
    RATE_LIMIT_SECONDS = 3.0
    MAX_PAGES = 1
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False
    PAGE_SIZE = 50

    def __init__(self):
        super().__init__()
        self._school = get_school("isb_basel")

    def build_listing_url(self, page: int, query: str) -> str:
        return ISB_BOARD_URL

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        if not self._school:
            return []

        container = soup.select_one("div.fsPostElement")
        if container is None:
            return []
        if container.select_one(".fsElementEmpty"):
            # "No post to display." — board vacío
            return []

        out: list[dict] = []
        # Cada post puede ser article.fsArticle o div.fsPost
        for post in container.select(
            "article.fsArticle, .fsPost, .fsConstituentList li"
        ):
            stub = self._parse_post(post)
            if stub:
                out.append(stub)
        return out

    def _parse_post(self, post: Tag) -> dict | None:
        title_el = (
            post.select_one(".fsPostTitle a, .fsArticleTitle a")
            or post.select_one("a.fsPostLink, a.fsArticleLink")
            or post.select_one("h2 a, h3 a")
        )
        if not title_el:
            return None

        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        if not title or not href:
            return None

        url = href if href.startswith("http") else f"{ISB_BASE}{href}"
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
