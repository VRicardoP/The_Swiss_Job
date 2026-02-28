"""Scraper for Financejobs.ch — finance sector jobs in Switzerland.

Uses Next.js __NEXT_DATA__ embedded JSON rather than DOM scraping,
which is more reliable than CSS selectors on styled-components.
"""

import json
import logging

from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

BASE_URL = "https://www.financejobs.ch"


class FinancejobsScraper(BaseScraper):
    SOURCE_NAME = "financejobs"
    LISTING_URL = f"{BASE_URL}/de/jobs"
    RATE_LIMIT_SECONDS = 2.0
    MAX_PAGES = 10
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False  # __NEXT_DATA__ contains all info
    PAGE_SIZE = 20

    def build_listing_url(self, page: int, query: str) -> str:
        return f"{self.LISTING_URL}?page={page}"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract jobs from __NEXT_DATA__ JSON embedded in the page."""
        script_el = soup.select_one("script#__NEXT_DATA__")
        if not script_el or not script_el.string:
            logger.warning("financejobs: no __NEXT_DATA__ found")
            return []

        try:
            data = json.loads(script_el.string)
        except json.JSONDecodeError as e:
            logger.error("financejobs: failed to parse __NEXT_DATA__: %s", e)
            return []

        # Navigate the Next.js data structure (verified 2026-02-28)
        # Path: props.initialProps.pageProps.jobsSSR.jobs
        jobs_data = (
            data.get("props", {})
            .get("initialProps", {})
            .get("pageProps", {})
            .get("jobsSSR", {})
            .get("jobs", [])
        )

        stubs: list[dict] = []
        for job in jobs_data:
            if not isinstance(job, dict):
                continue

            job_id = job.get("jobId") or job.get("jcJobId") or ""
            title = job.get("title", "")
            company = job.get("companyName", "") or "Unknown"
            location = job.get("location", "")
            description = job.get("description") or job.get("summary") or ""
            employment_type = job.get("workload") or ""
            logo = job.get("companyLogo") or job.get("logoImage") or None

            url = f"{BASE_URL}/de/job/{job_id}" if job_id else ""

            if not title or not url:
                continue

            stubs.append(
                {
                    "title": strip_html_tags(title).strip(),
                    "company": company.strip(),
                    "location": location.strip(),
                    "url": url,
                    "description": strip_html_tags(description),
                    "employment_type": employment_type,
                    "salary_original": job.get("salary"),
                    "logo": logo,
                    "tags": job.get("tags", []),
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
