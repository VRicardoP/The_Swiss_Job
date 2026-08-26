"""Provider for Remotive remote jobs API."""

import logging

import httpx

from services.job_service import BaseJobProvider
from utils.dates import parse_published_at
from utils import fetch_diagnostics as diag
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)


class RemotiveProvider(BaseJobProvider):
    """Fetch remote jobs from the Remotive API."""

    SOURCE_NAME = "remotive"
    API_URL = "https://remotive.com/api/remote-jobs"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from Remotive filtered by search term."""
        params: dict[str, str | int] = {"limit": 200}
        if query:
            params["search"] = query

        async with httpx.AsyncClient() as client:
            data = await self._circuit.call(
                lambda: fetch_with_retry(client, self.API_URL, params=params)
            )

        # G4/P2-8: un 200 ilegible ya no se confunde con "no hay ofertas".
        raw_jobs = diag.json_items(data, self.API_URL, self.SOURCE_NAME, key="jobs")
        if not raw_jobs:
            return []

        results: list[dict] = []
        results.extend(self._process_raw_jobs(raw_jobs))

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Remotive API response into the unified job schema."""
        title = (raw.get("title") or "").strip()
        company = (raw.get("company_name") or "").strip()
        url = (raw.get("url") or "").strip()
        description = strip_html_tags(raw.get("description", ""))
        location_raw = raw.get("candidate_required_location", "")
        employment_type = raw.get("job_type", None)

        # Combine API tags with extracted skills
        api_tags = raw.get("tags", []) or []
        extracted_tags = extract_job_skills(title, description)
        # Merge: API tags first, then extracted (deduplicated, case-insensitive)
        seen_lower: set[str] = set()
        merged_tags: list[str] = []
        for tag in api_tags + extracted_tags:
            tag_str = str(tag).strip()
            if tag_str and tag_str.lower() not in seen_lower:
                seen_lower.add(tag_str.lower())
                merged_tags.append(tag_str)

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
            # Fecha del PORTAL (publication_date, ISO8601 sin zona ⇒ UTC).
            "published_at": parse_published_at(raw.get("publication_date")),
        }
