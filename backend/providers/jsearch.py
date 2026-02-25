"""Provider for JSearch (RapidAPI) jobs API."""

import logging

import httpx

from config import settings
from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)


class JSearchProvider(BaseJobProvider):
    """Fetch jobs from the JSearch RapidAPI endpoint (Switzerland-focused)."""

    SOURCE_NAME = "jsearch"
    API_URL = "https://jsearch.p.rapidapi.com/search"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from JSearch RapidAPI, hardcoded to Switzerland."""
        api_key = settings.JSEARCH_RAPIDAPI_KEY
        if not api_key:
            logger.warning("JSEARCH_RAPIDAPI_KEY not configured, skipping JSearch")
            return []

        headers = {
            "x-rapidapi-host": "jsearch.p.rapidapi.com",
            "x-rapidapi-key": api_key,
        }
        params: dict[str, str | int] = {
            "query": query or "software developer",
            "page": 1,
            "num_pages": 3,
            "country": "ch",
        }

        async with httpx.AsyncClient() as client:
            data = await self._circuit.call(
                lambda: fetch_with_retry(
                    client, self.API_URL, headers=headers, params=params
                )
            )

        if not data:
            return []

        raw_jobs = data.get("data", []) or []
        results: list[dict] = []
        results.extend(self._process_raw_jobs(raw_jobs))

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw JSearch API response into the unified job schema."""
        title = raw.get("job_title", "").strip()
        company = raw.get("employer_name", "").strip()
        url = raw.get("job_apply_link", "").strip()
        description = strip_html_tags(raw.get("job_description", ""))
        logo = raw.get("employer_logo", None)
        is_remote = bool(raw.get("job_is_remote", False))
        employment_type = raw.get("job_employment_type", None)

        # Build location from city, state, country (skip empty parts)
        location_parts = [
            raw.get("job_city", ""),
            raw.get("job_state", ""),
            raw.get("job_country", ""),
        ]
        location_raw = ", ".join(p.strip() for p in location_parts if p and p.strip())

        tags = extract_job_skills(title, description)

        # Extract salary information if present
        salary_min = raw.get("job_min_salary")
        salary_max = raw.get("job_max_salary")
        salary_currency = raw.get("job_salary_currency")
        salary_period = raw.get("job_salary_period")
        salary_original = None

        if salary_min is not None or salary_max is not None:
            parts = []
            if salary_min is not None:
                parts.append(str(salary_min))
            if salary_max is not None:
                parts.append(str(salary_max))
            amount_str = "-".join(parts)
            currency_str = salary_currency or ""
            period_str = f"/{salary_period}" if salary_period else ""
            salary_original = f"{amount_str} {currency_str}{period_str}".strip()

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
            "logo": logo,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": salary_original,
            "salary_currency": salary_currency,
            "salary_period": salary_period,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": employment_type,
        }
