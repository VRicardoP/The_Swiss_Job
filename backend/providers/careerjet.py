"""Provider for Careerjet job search API."""

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


class CareerjetProvider(BaseJobProvider):
    """Fetch jobs from the Careerjet public search API."""

    SOURCE_NAME = "careerjet"
    API_URL = "https://public.api.careerjet.net/search"
    MAX_PAGES = 3
    PAGE_SIZE = 50

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from Careerjet, paginating up to MAX_PAGES."""
        affid = settings.CAREERJET_AFFID
        if not affid:
            logger.warning("Careerjet affiliate ID not configured, skipping provider")
            return []

        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for page in range(1, self.MAX_PAGES + 1):
                params = {
                    "affid": affid,
                    "user_ip": "1.0.0.1",
                    "user_agent": self.USER_AGENT,
                    "locale_code": "en",
                    "keywords": query,
                    "location": location,
                    "page": page,
                    "pagesize": self.PAGE_SIZE,
                    "sort": "date",
                }

                try:
                    data = await self._circuit.call(
                        lambda p=params: fetch_with_retry(
                            client, self.API_URL, params=p
                        )
                    )
                except (CircuitBreakerOpen, httpx.HTTPError, json.JSONDecodeError) as e:
                    logger.error("Careerjet fetch error on page %d: %s", page, e)
                    break

                if not data:
                    break

                # Verify response type
                resp_type = data.get("type", "")
                if resp_type != "JOBS":
                    logger.warning(
                        "Careerjet response type: %s (expected JOBS)", resp_type
                    )
                    break

                raw_jobs = data.get("jobs", [])
                if not raw_jobs:
                    break

                results.extend(self._process_raw_jobs(raw_jobs))

                # Check if we've reached the last page
                total_pages = data.get("pages", 1)
                if page >= total_pages:
                    break

                # Delay between pages to avoid rate limiting
                if page < self.MAX_PAGES:
                    await asyncio.sleep(0.5)

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Careerjet API response into the unified job schema."""
        title = (raw.get("title") or "").strip()
        company = (raw.get("company") or "").strip()
        url = (raw.get("url") or "").strip()
        description_html = raw.get("description", "")
        description = strip_html_tags(description_html)
        location_raw = (raw.get("locations") or "").strip()
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
            "employment_type": None,
        }
