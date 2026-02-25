"""Provider for Himalayas remote jobs API."""

import asyncio
import logging

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

MAX_PAGES = 3
PAGE_SIZE = 50
PAGE_DELAY_SECONDS = 0.5


class HimalayasProvider(BaseJobProvider):
    """Fetch remote jobs from the Himalayas API (paginated, up to 3 pages)."""

    SOURCE_NAME = "himalayas"
    API_URL = "https://himalayas.app/jobs/api"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from Himalayas, paginating up to 3 pages."""
        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for page in range(MAX_PAGES):
                offset = page * PAGE_SIZE

                data = await self._circuit.call(
                    lambda o=offset: fetch_with_retry(
                        client,
                        self.API_URL,
                        params={"limit": PAGE_SIZE, "offset": o},
                    )
                )

                if not data:
                    break

                raw_jobs = data.get("jobs", [])
                if not raw_jobs:
                    break

                results.extend(self._process_raw_jobs(raw_jobs))

                # Polite delay between pages
                if page < MAX_PAGES - 1:
                    await asyncio.sleep(PAGE_DELAY_SECONDS)

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Himalayas API response into the unified job schema."""
        title = raw.get("title", "").strip()
        company = raw.get("companyName", "").strip()
        url = raw.get("applicationLink", "").strip()
        description = strip_html_tags(raw.get("excerpt", ""))
        logo = raw.get("companyLogo", None)
        employment_type = raw.get("employmentType", None)

        # Location: first element of locationRestrictions, or "Worldwide"
        location_restrictions = raw.get("locationRestrictions", []) or []
        location_raw = (
            location_restrictions[0] if location_restrictions else "Worldwide"
        )

        # Tags: from categories + extracted skills
        categories = raw.get("categories", []) or []
        extracted_tags = extract_job_skills(title, description)
        seen_lower: set[str] = set()
        merged_tags: list[str] = []
        for tag in categories + extracted_tags:
            tag_str = str(tag).strip()
            if tag_str and tag_str.lower() not in seen_lower:
                seen_lower.add(tag_str.lower())
                merged_tags.append(tag_str)

        # Salary: build salary_original from minSalary/maxSalary/currency
        min_salary = raw.get("minSalary")
        max_salary = raw.get("maxSalary")
        currency = raw.get("currency")
        salary_original = None
        salary_currency = None
        salary_period = None

        if min_salary and int(min_salary) > 0 or max_salary and int(max_salary) > 0:
            parts = []
            if min_salary and int(min_salary) > 0:
                parts.append(str(min_salary))
            if max_salary and int(max_salary) > 0:
                parts.append(str(max_salary))
            amount_str = "-".join(parts)
            salary_currency = currency or "USD"
            salary_period = "year"
            salary_original = f"{amount_str} {salary_currency}/{salary_period}"

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
            "salary_currency": salary_currency,
            "salary_period": salary_period,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": employment_type,
        }
