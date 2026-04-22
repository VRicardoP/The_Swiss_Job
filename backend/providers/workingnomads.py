"""Provider for Working Nomads remote jobs (public JSON API).

Working Nomads aggregates 30,000+ remote jobs across all categories.
Public API requires no authentication.

API: https://www.workingnomads.com/api/exposed_jobs/
"""

import logging

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

API_URL = "https://www.workingnomads.com/api/exposed_jobs/"

# Categorías relevantes para el perfil — descartar las puramente tech
_RELEVANT_CATEGORIES = {
    "writing",
    "content",
    "copywriting",
    "editing",
    "translation",
    "localization",
    "customer support",
    "customer service",
    "human resources",
    "hr",
    "recruiting",
    "operations",
    "admin",
    "project management",
    "product",
    "marketing",
    "social media",
    "education",
    "elearning",
    "teaching",
    "research",
    "data",
    "qa",
    "quality assurance",
    "all other remote",
    "non-tech",
    "business",
    "finance",
    "accounting",
}

_TECH_EXCLUDE_TITLES = {
    "software engineer", "backend engineer", "frontend engineer",
    "full stack", "fullstack", "devops", "sre", "site reliability",
    "ml engineer", "data engineer", "cloud engineer", "platform engineer",
    "mobile developer", "ios developer", "android developer",
    "blockchain", "cybersecurity", "security engineer", "embedded",
    "firmware", "hardware engineer", "network engineer", "infrastructure",
}


class WorkingNomadsProvider(BaseJobProvider):
    """Fetch remote jobs from Working Nomads public JSON API."""

    SOURCE_NAME = "workingnomads"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from Working Nomads API."""
        async with httpx.AsyncClient() as client:
            data = await self._circuit.call(
                lambda: fetch_with_retry(
                    client,
                    API_URL,
                    timeout=25.0,
                )
            )

        if not data or not isinstance(data, list):
            return []

        all_jobs = self._process_raw_jobs(data)
        filtered = self._filter_relevant(all_jobs)

        if query:
            q_lower = query.lower()
            filtered = [
                j for j in filtered
                if q_lower in f"{j['title']} {j['description']}".lower()
            ]

        return self._finalize_fetch(filtered)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a Working Nomads API entry into the unified job schema."""
        title = (raw.get("title") or "").strip()
        company = (raw.get("company_name") or "").strip()
        url = (raw.get("url") or "").strip()
        description_html = raw.get("description") or ""
        description = strip_html_tags(description_html)
        location_raw = (raw.get("location") or "Remote / Worldwide").strip()
        category = (raw.get("category_name") or "").strip()

        # Tags: combinar category + tags del API + extraídos
        # La API puede devolver tags como lista o como string CSV
        raw_tags = raw.get("tags") or []
        if isinstance(raw_tags, str):
            api_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        else:
            api_tags = list(raw_tags)
        extracted = extract_job_skills(title, description)
        all_tags = []
        seen: set[str] = set()
        for tag in ([category] if category else []) + api_tags + extracted:
            tag_str = str(tag).strip()
            if tag_str and tag_str.lower() not in seen:
                seen.add(tag_str.lower())
                all_tags.append(tag_str)

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location_raw if location_raw else "Remote / Worldwide",
            "canton": extract_canton(location_raw),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": True,
            "tags": all_tags[: self.MAX_TAGS],
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": "en",
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }

    @staticmethod
    def _filter_relevant(jobs: list[dict]) -> list[dict]:
        """Conserva sólo roles no puramente técnicos."""
        result = []
        for job in jobs:
            title_lower = job.get("title", "").lower()
            if any(kw in title_lower for kw in _TECH_EXCLUDE_TITLES):
                continue
            result.append(job)
        return result
