"""Provider for Jooble job aggregator API."""

import asyncio
import json
import logging

import httpx

from config import settings
from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)


class JoobleProvider(BaseJobProvider):
    """Fetch jobs from the Jooble API (POST-based, requires API key)."""

    SOURCE_NAME = "jooble"
    API_URL_TEMPLATE = "https://jooble.org/api/{api_key}"
    MAX_PAGES = 3

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from Jooble, paginating up to MAX_PAGES."""
        api_key = settings.JOOBLE_API_KEY
        if not api_key:
            logger.warning("Jooble API key not configured, skipping provider")
            return []

        api_url = self.API_URL_TEMPLATE.format(api_key=api_key)
        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for page in range(1, self.MAX_PAGES + 1):
                json_body = {
                    "keywords": query,
                    "location": location,
                    "page": str(page),
                }

                try:
                    data = await self._circuit.call(
                        lambda body=json_body: fetch_with_retry(
                            client,
                            api_url,
                            method="POST",
                            json_body=body,
                        )
                    )
                except (CircuitBreakerOpen, httpx.HTTPError, json.JSONDecodeError) as e:
                    logger.error("Jooble fetch error on page %d: %s", page, e)
                    break

                if not data:
                    break

                raw_jobs = data.get("jobs", [])
                if not raw_jobs:
                    break

                results.extend(self._process_raw_jobs(raw_jobs))

                # Check if there are more results
                total_count = data.get("totalCount", 0)
                if len(results) >= total_count:
                    break

                # Delay between pages
                if page < self.MAX_PAGES:
                    await asyncio.sleep(0.5)

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Jooble API response into the unified job schema."""
        title = (raw.get("title") or "").strip()
        company = (raw.get("company") or "").strip()
        url = (raw.get("link") or "").strip()
        snippet = raw.get("snippet", "")
        description = strip_html_tags(snippet)
        location_raw = (raw.get("location") or "").strip()
        employment_type = raw.get("type") or None
        salary_raw = raw.get("salary") or ""

        tags = extract_job_skills(title, description)

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location_raw if location_raw else "Switzerland",
            "canton": extract_canton(location_raw),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": False,
            "tags": tags,
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": salary_raw if salary_raw else None,
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": employment_type,
        }
