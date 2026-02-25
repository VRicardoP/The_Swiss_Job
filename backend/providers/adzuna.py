"""Provider for Adzuna jobs API (multi-country)."""

import asyncio
import logging

import httpx

from config import settings
from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

# Adzuna does not support "ch" — fetch from neighbouring countries instead
ADZUNA_COUNTRIES = ["de", "at", "gb"]
PAGES_PER_COUNTRY = 2
PAGE_DELAY_SECONDS = 0.5
RESULTS_PER_PAGE = 50


class AdzunaProvider(BaseJobProvider):
    """Fetch jobs from the Adzuna API across multiple countries."""

    SOURCE_NAME = "adzuna"
    API_BASE = "https://api.adzuna.com/v1/api/jobs"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from Adzuna for DE, AT, and GB (2 pages each)."""
        app_id = settings.ADZUNA_APP_ID
        app_key = settings.ADZUNA_APP_KEY
        if not app_id or not app_key:
            logger.warning(
                "ADZUNA_APP_ID / ADZUNA_APP_KEY not configured, skipping Adzuna"
            )
            return []

        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for country in ADZUNA_COUNTRIES:
                for page in range(1, PAGES_PER_COUNTRY + 1):
                    url = f"{self.API_BASE}/{country}/search/{page}"
                    params: dict[str, str | int] = {
                        "app_id": app_id,
                        "app_key": app_key,
                        "results_per_page": RESULTS_PER_PAGE,
                        "what": query or "software developer",
                        "what_or": "software developer engineer",
                    }

                    # Capture loop vars with defaults to avoid late-binding issues
                    data = await self._circuit.call(
                        lambda u=url, p=params: fetch_with_retry(client, u, params=p)
                    )

                    if not data:
                        break

                    raw_jobs = data.get("results", [])
                    if not raw_jobs:
                        break

                    results.extend(self._process_raw_jobs(raw_jobs))

                    # Polite delay between requests
                    await asyncio.sleep(PAGE_DELAY_SECONDS)

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Adzuna API response into the unified job schema."""
        title = strip_html_tags(raw.get("title", "")).strip()
        url = raw.get("redirect_url", "").strip()
        description = strip_html_tags(raw.get("description", ""))

        # Company is an object with display_name
        company_obj = raw.get("company", {}) or {}
        company = company_obj.get("display_name", "").strip()

        # Location: prefer area[0] if exists, else location.display_name
        location_obj = raw.get("location", {}) or {}
        area = location_obj.get("area", []) or []
        location_raw = area[0] if area else location_obj.get("display_name", "")

        # Category label -> employment_type
        category_obj = raw.get("category", {}) or {}
        employment_type = category_obj.get("label", None)

        # Contract info
        contract_type = raw.get("contract_type", None)
        contract_time = raw.get("contract_time", None)

        tags = extract_job_skills(title, description)

        # Salary
        salary_min = raw.get("salary_min")
        salary_max = raw.get("salary_max")
        salary_original = None

        if salary_min is not None or salary_max is not None:
            parts = []
            if salary_min is not None:
                parts.append(str(int(salary_min)))
            if salary_max is not None:
                parts.append(str(int(salary_max)))
            salary_original = "-".join(parts)

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
            "remote": False,
            "tags": tags,
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": salary_original,
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": contract_type or contract_time,
            "employment_type": employment_type,
        }
