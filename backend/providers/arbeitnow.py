"""Provider for Arbeitnow job board API."""

import asyncio
import logging

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

MAX_PAGES = 3
PAGE_DELAY_SECONDS = 0.5


class ArbeitnowProvider(BaseJobProvider):
    """Fetch jobs from the Arbeitnow job board API (paginated, up to 3 pages)."""

    SOURCE_NAME = "arbeitnow"
    API_URL = "https://www.arbeitnow.com/api/job-board-api"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from Arbeitnow, paginating up to 3 pages."""
        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for page in range(1, MAX_PAGES + 1):
                data = await self._circuit.call(
                    lambda p=page: fetch_with_retry(
                        client, self.API_URL, params={"page": p}
                    )
                )

                if not data:
                    break

                raw_jobs = data.get("data", [])
                if not raw_jobs:
                    break

                results.extend(self._process_raw_jobs(raw_jobs))

                # Polite delay between pages
                if page < MAX_PAGES:
                    await asyncio.sleep(PAGE_DELAY_SECONDS)

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Arbeitnow API response into the unified job schema."""
        title = raw.get("title", "").strip()
        company = raw.get("company_name", "").strip()
        url = raw.get("url", "").strip()
        description = strip_html_tags(raw.get("description", ""))
        location_raw = raw.get("location", "")
        is_remote = bool(raw.get("remote", False))

        # Combine API tags with extracted skills
        api_tags = raw.get("tags", []) or []
        extracted_tags = extract_job_skills(title, description)
        seen_lower: set[str] = set()
        merged_tags: list[str] = []
        for tag in api_tags + extracted_tags:
            tag_str = str(tag).strip()
            if tag_str and tag_str.lower() not in seen_lower:
                seen_lower.add(tag_str.lower())
                merged_tags.append(tag_str)

        # Join job_types list into a single string
        job_types = raw.get("job_types", []) or []
        employment_type = ", ".join(job_types) if job_types else None

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
            "tags": merged_tags[: self.MAX_TAGS],
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
