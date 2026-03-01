"""Scraper for TES.com — international education and teaching jobs in Switzerland.

Uses Next.js __NEXT_DATA__ embedded JSON with tRPC state.
Pagination: 1 job per page (server-enforced limit), ~32 total.
"""

import json
import logging

from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper
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

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract jobs from __NEXT_DATA__ tRPC state."""
        script_el = soup.select_one("script#__NEXT_DATA__")
        if not script_el or not script_el.string:
            logger.warning("tes: no __NEXT_DATA__ found")
            return []

        try:
            data = json.loads(script_el.string)
        except json.JSONDecodeError as e:
            logger.error("tes: failed to parse __NEXT_DATA__: %s", e)
            return []

        # Navigate: props.pageProps.trpcState.json.queries[0].state.data.jobs
        try:
            queries = (
                data.get("props", {})
                .get("pageProps", {})
                .get("trpcState", {})
                .get("json", {})
                .get("queries", [])
            )
            if not queries:
                return []
            jobs_data = queries[0].get("state", {}).get("data", {}).get("jobs", [])
        except (IndexError, AttributeError):
            logger.warning("tes: unexpected __NEXT_DATA__ structure")
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
        }
