"""Provider for RemoteOK jobs API."""

import logging

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

# RemoteOK reports salaries in thousands; multiply if below this threshold
SALARY_THOUSANDS_THRESHOLD = 1000


class RemoteOKProvider(BaseJobProvider):
    """Fetch remote jobs from the RemoteOK API."""

    SOURCE_NAME = "remoteok"
    API_URL = "https://remoteok.com/api"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from RemoteOK (single endpoint, no params)."""
        headers = self.DEFAULT_HEADERS

        async with httpx.AsyncClient() as client:
            data = await self._circuit.call(
                lambda: fetch_with_retry(client, self.API_URL, headers=headers)
            )

        if not data:
            return []

        # Response is a JSON array; index 0 is metadata, actual jobs start at index 1
        if isinstance(data, list) and len(data) > 1:
            raw_jobs = data[1:]
        else:
            return []

        results: list[dict] = []
        results.extend(self._process_raw_jobs(raw_jobs))

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw RemoteOK API response into the unified job schema."""
        title = raw.get("position", "").strip()
        company = raw.get("company", "").strip()
        location_raw = raw.get("location", "")
        description = strip_html_tags(raw.get("description", ""))
        logo = raw.get("logo", None)

        # Prefer apply_url; fall back to url; construct from slug as last resort
        url = raw.get("apply_url", "").strip()
        if not url:
            url = raw.get("url", "").strip()
        if not url:
            slug = raw.get("slug", "")
            if slug:
                url = f"https://remoteok.com/remote-jobs/{slug}"

        # Tags from the API response
        api_tags = raw.get("tags", []) or []
        extracted_tags = extract_job_skills(title, description)
        seen_lower: set[str] = set()
        merged_tags: list[str] = []
        for tag in api_tags + extracted_tags:
            tag_str = str(tag).strip()
            if tag_str and tag_str.lower() not in seen_lower:
                seen_lower.add(tag_str.lower())
                merged_tags.append(tag_str)

        # Salary: RemoteOK uses thousands — multiply if < 1000
        salary_min = raw.get("salary_min")
        salary_max = raw.get("salary_max")
        salary_original = None

        if salary_min is not None:
            try:
                salary_min = int(salary_min)
                if salary_min > 0 and salary_min < SALARY_THOUSANDS_THRESHOLD:
                    salary_min *= 1000
            except (ValueError, TypeError):
                salary_min = None

        if salary_max is not None:
            try:
                salary_max = int(salary_max)
                if salary_max > 0 and salary_max < SALARY_THOUSANDS_THRESHOLD:
                    salary_max *= 1000
            except (ValueError, TypeError):
                salary_max = None

        if salary_min or salary_max:
            parts = []
            if salary_min:
                parts.append(str(salary_min))
            if salary_max:
                parts.append(str(salary_max))
            salary_original = "-".join(parts) + " USD/year"

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
            "logo": logo,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": salary_original,
            "salary_currency": "USD" if salary_original else None,
            "salary_period": "year" if salary_original else None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }
