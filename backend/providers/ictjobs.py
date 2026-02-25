"""Provider for ICTJobs.ch (WordPress REST API with ACF fields)."""

import asyncio
import json
import logging

import httpx

from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)


class ICTJobsProvider(BaseJobProvider):
    """Fetch ICT jobs from ictjobs.ch WordPress API with embedded taxonomies."""

    SOURCE_NAME = "ictjobs"
    API_URL = "https://ictjobs.ch/wp-json/wp/v2/posts"
    MAX_PAGES = 3
    PAGE_SIZE = 50

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from ICTJobs, paginating up to MAX_PAGES."""
        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for page in range(1, self.MAX_PAGES + 1):
                params: dict = {
                    "page": page,
                    "per_page": self.PAGE_SIZE,
                    "_embed": "",  # Resolve taxonomy terms
                }

                try:
                    data = await self._circuit.call(
                        lambda p=params: fetch_with_retry(
                            client,
                            self.API_URL,
                            params=p,
                            timeout=30.0,  # Higher timeout due to _embed payload
                        )
                    )
                except (CircuitBreakerOpen, httpx.HTTPError, json.JSONDecodeError) as e:
                    logger.error("ICTJobs fetch error on page %d: %s", page, e)
                    break

                if not data:
                    break

                if not isinstance(data, list):
                    logger.warning(
                        "ICTJobs unexpected response type: %s",
                        type(data).__name__,
                    )
                    break

                if not data:
                    break

                results.extend(self._process_raw_jobs(data))

                # Delay between pages to avoid rate limiting
                if page < self.MAX_PAGES and len(data) == self.PAGE_SIZE:
                    await asyncio.sleep(0.5)
                else:
                    break

        return self._finalize_fetch(results)

    def _extract_embedded_terms(self, raw: dict) -> dict:
        """Extract taxonomy terms from _embedded.wp:term arrays.

        Returns a dict with keys: tags, location_terms, employment_type.
        """
        embedded = raw.get("_embedded", {})
        wp_terms = embedded.get("wp:term", [])

        tag_names: list[str] = []
        location_terms: list[str] = []
        employment_type: str | None = None

        for term_group in wp_terms:
            if not isinstance(term_group, list):
                continue
            for term in term_group:
                if not isinstance(term, dict):
                    continue
                taxonomy = term.get("taxonomy", "")
                name = term.get("name", "").strip()
                if not name:
                    continue

                if taxonomy == "post_tag":
                    tag_names.append(name)
                elif taxonomy in ("ctx_work_location", "ctx_flake_location"):
                    location_terms.append(name)
                elif taxonomy == "ctx_employment_type" and not employment_type:
                    employment_type = name

        return {
            "tags": tag_names,
            "location_terms": location_terms,
            "employment_type": employment_type,
        }

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw ICTJobs WP post into the unified job schema."""
        # Title
        title_obj = raw.get("title", {})
        title = strip_html_tags(
            title_obj.get("rendered", "") if isinstance(title_obj, dict) else ""
        ).strip()

        # ACF fields
        acf = raw.get("acf", {}) or {}
        intro = acf.get("intro", "") or ""
        acf_description = acf.get("description", "") or ""
        description = strip_html_tags(f"{intro} {acf_description}").strip()

        acf_location = acf.get("location", "") or "Switzerland"
        is_remote = bool(acf.get("has_home_office", False))

        # URL: use direct_link if use_direct_link is set, otherwise WP link
        use_direct = acf.get("use_direct_link", False)
        direct_link = acf.get("direct_link", "")
        url = direct_link if use_direct and direct_link else raw.get("link", "")

        # Salary from ACF
        salary_min = acf.get("salary_min")
        salary_max = acf.get("salary_max")

        # Embedded taxonomy terms
        terms = self._extract_embedded_terms(raw)

        # Company: try first tag as company name (common pattern for ICTJobs)
        company = terms["tags"][0] if terms["tags"] else "Unknown"

        # Location: prefer ACF location, supplement with taxonomy location terms
        if not acf_location or acf_location == "Switzerland":
            if terms["location_terms"]:
                acf_location = ", ".join(terms["location_terms"])

        # Tags: embedded post_tag names + extracted skills
        extracted_skills = extract_job_skills(title, description)
        all_tags = list(dict.fromkeys(terms["tags"] + extracted_skills))[
            : self.MAX_TAGS
        ]

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": acf_location,
            "canton": extract_canton(acf_location),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": is_remote,
            "tags": all_tags,
            "logo": None,
            "salary_min_chf": int(salary_min) if salary_min else None,
            "salary_max_chf": int(salary_max) if salary_max else None,
            "salary_original": None,
            "salary_currency": "CHF" if salary_min or salary_max else None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": terms["employment_type"],
        }
