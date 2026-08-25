"""Scraper for TES.com — international education and teaching jobs in Switzerland.

Uses Next.js __NEXT_DATA__ embedded JSON with tRPC state.
Pagination: 1 job per page (server-enforced limit), ~32 total.
"""

import json
import logging

from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper
from utils import fetch_diagnostics as diag
from utils.dates import parse_published_at
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

BASE_URL = "https://www.tes.com"


class TESScraper(BaseScraper):
    SOURCE_NAME = "tes"
    LISTING_URL = f"{BASE_URL}/jobs/browse/switzerland"
    RATE_LIMIT_SECONDS = 2.0
    MAX_PAGES = 35
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False  # __NEXT_DATA__ contains all info
    PAGE_SIZE = 1  # Server enforces limit=1 per page

    def build_listing_url(self, page: int, query: str) -> str:
        return f"{self.LISTING_URL}?page={page}"

    def _record_structure_failure(self, detail: str) -> None:
        """Registra un fallo de estructura como error de fetch VISIBLE.

        G1/P2-5: tes ni importaba fetch_diagnostics — sin __NEXT_DATA__, JSON
        ilegible o ruta tRPC cambiada devolvía [] en silencio y la fuente
        ROTA salía `empty` (clase VD.7). Mismo patrón que financejobs.
        """
        logger.error("tes: %s", detail)
        diag.record(diag.KIND_NETWORK, self.LISTING_URL, detail=detail)

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract jobs from __NEXT_DATA__ tRPC state."""
        script_el = soup.select_one("script#__NEXT_DATA__")
        if not script_el or not script_el.string:
            self._record_structure_failure("no __NEXT_DATA__ found")
            return []

        try:
            data = json.loads(script_el.string)
        except (ValueError, RecursionError) as e:
            # ValueError (no JSONDecodeError): un número gigante o un cuerpo
            # no-UTF8 lanzan ValueError PLANO que antes escapaba (G1/P2-5).
            self._record_structure_failure(f"failed to parse __NEXT_DATA__: {e}")
            return []

        # Navigate: props.pageProps.trpcState.json.queries[n].state.data.jobs
        try:
            queries = (
                data.get("props", {})
                .get("pageProps", {})
                .get("trpcState", {})
                .get("json", {})
                .get("queries", [])
            )
            if not queries or not isinstance(queries, list):
                self._record_structure_failure(
                    "__NEXT_DATA__ sin trpcState.queries: estructura desconocida"
                )
                return []
            # G1/P2-5: `queries[0]` era un índice mágico — se busca la query
            # que realmente trae `state.data.jobs`, esté donde esté.
            jobs_data = None
            for query_entry in queries:
                if not isinstance(query_entry, dict):
                    continue
                candidate = query_entry.get("state", {}).get("data", {})
                if isinstance(candidate, dict) and "jobs" in candidate:
                    jobs_data = candidate["jobs"]
                    break
            if not isinstance(jobs_data, list):
                self._record_structure_failure(
                    "ninguna query tRPC trae state.data.jobs: estructura desconocida"
                )
                return []
        except (IndexError, AttributeError, TypeError):
            self._record_structure_failure("unexpected __NEXT_DATA__ structure")
            return []

        stubs: list[dict] = []
        for job in jobs_data:
            if not isinstance(job, dict):
                continue

            title = job.get("title", "")
            if not title:
                continue

            employer = job.get("employer") or {}
            company = employer.get("name", "") or "Unknown"

            images = employer.get("images") or {}
            logo = images.get("logo")

            canonical = job.get("canonicalUrl", "")
            url = f"{BASE_URL}{canonical}" if canonical else ""
            if not url:
                continue

            description = strip_html_tags(job.get("shortDescription", ""))
            location = job.get("displayLocation", "Switzerland")

            # Contract info
            contract_terms = job.get("contractTerms", [])
            contract_types = job.get("contractTypes", [])

            # Fecha de publicación del anuncio (advert.startDate, ISO8601 Z)
            advert = job.get("advert") or {}
            date_posted = advert.get("startDate")

            # Salary
            salary = job.get("salary") or {}
            salary_range = salary.get("range", "")
            # Clean non-breaking spaces
            if salary_range:
                salary_range = salary_range.replace("\xa0", " ")

            stubs.append(
                {
                    "title": title.strip(),
                    "company": company.strip(),
                    "location": location.strip(),
                    "url": url,
                    "description": description,
                    "employment_type": contract_types[0] if contract_types else None,
                    "contract_term": contract_terms[0] if contract_terms else None,
                    "salary_original": salary_range if salary_range else None,
                    "logo": logo,
                    "date_posted": date_posted,
                }
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
