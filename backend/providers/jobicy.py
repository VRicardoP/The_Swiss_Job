"""Provider for Jobicy remote jobs API."""

import logging

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)


class JobicyProvider(BaseJobProvider):
    """Fetch remote jobs from the Jobicy API."""

    SOURCE_NAME = "jobicy"
    API_URL = "https://jobicy.com/api/v2/remote-jobs"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from Jobicy filtered by tag."""
        params: dict[str, str | int] = {"count": 50}
        if query:
            params["tag"] = query
        if location and location.lower() != "switzerland":
            params["geo"] = location

        async with httpx.AsyncClient() as client:
            data = await self._circuit.call(
                lambda: fetch_with_retry(client, self.API_URL, params=params)
            )

        if not data:
            return []

        raw_jobs = data.get("jobs", [])
        results: list[dict] = []
        results.extend(self._process_raw_jobs(raw_jobs))

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Jobicy API response into the unified job schema."""
        title = raw.get("jobTitle", "").strip()
        company = raw.get("companyName", "").strip()
        url = raw.get("url", "").strip()
        description = strip_html_tags(raw.get("jobDescription", ""))
        location_raw = raw.get("jobGeo", "") or raw.get("country", "")
        tags = extract_job_skills(title, description)
        employment_type = raw.get("jobType", None)

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
            "remote": True,
            "tags": tags,
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": employment_type,
        }
