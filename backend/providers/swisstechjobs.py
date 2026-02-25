"""Provider for SwissTechJobs (WordPress REST API)."""

import asyncio
import json
import logging

import httpx

from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)


class SwissTechJobsProvider(BaseJobProvider):
    """Fetch tech jobs from SwissTechJobs WordPress API."""

    SOURCE_NAME = "swisstechjobs"
    API_URL = "https://www.swisstechjobs.com/wp-json/wp/v2/job-listings"
    MAX_PAGES = 2
    PAGE_SIZE = 100

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from SwissTechJobs, paginating up to MAX_PAGES."""
        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for page in range(1, self.MAX_PAGES + 1):
                params: dict = {"page": page, "per_page": self.PAGE_SIZE}

                try:
                    data = await self._circuit.call(
                        lambda p=params: fetch_with_retry(
                            client, self.API_URL, params=p
                        )
                    )
                except (CircuitBreakerOpen, httpx.HTTPError, json.JSONDecodeError) as e:
                    logger.error("SwissTechJobs fetch error on page %d: %s", page, e)
                    break

                if not data:
                    break

                # Response is a JSON array of WP posts
                if not isinstance(data, list):
                    logger.warning(
                        "SwissTechJobs unexpected response type: %s",
                        type(data).__name__,
                    )
                    break

                if not data:
                    break

                results.extend(self._process_raw_jobs(data))

                # Delay between pages to avoid rate limiting
                if page < self.MAX_PAGES and len(data) == self.PAGE_SIZE:
                    await asyncio.sleep(0.5)
                else:
                    break

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw SwissTechJobs WP post into the unified job schema."""
        # Title is in title.rendered (may contain HTML entities)
        title_obj = raw.get("title", {})
        title = strip_html_tags(
            title_obj.get("rendered", "") if isinstance(title_obj, dict) else ""
        ).strip()

        # Meta fields
        meta = raw.get("meta", {}) or {}
        company = meta.get("_company_name", "Unknown") or "Unknown"
        location_raw = meta.get("_job_location", "Switzerland") or "Switzerland"
        is_remote = bool(meta.get("_remote_position", False))
        application_url = meta.get("_application", "")
        salary_raw = meta.get("_job_salary", "")
        salary_currency = meta.get("_job_salary_currency", "") or "CHF"

        # URL: prefer the WP link, fallback to application URL
        url = raw.get("link", "") or application_url or ""

        # Description from content.rendered
        content_obj = raw.get("content", {})
        description_html = (
            content_obj.get("rendered", "") if isinstance(content_obj, dict) else ""
        )
        description = strip_html_tags(description_html)

        tags = extract_job_skills(title, description)

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location_raw,
            "canton": extract_canton(location_raw),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": is_remote,
            "tags": tags,
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": salary_raw if salary_raw else None,
            "salary_currency": salary_currency if salary_raw else None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }
