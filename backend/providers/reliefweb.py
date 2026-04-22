"""Provider for ReliefWeb jobs API (humanitarian, NGO, UN sector).

ReliefWeb is OCHA's public API — no authentication required.
Covers roles relevant to Alicia: programme assistants, documentation,
language assistants, admin support in international organisations.

API docs: https://apidoc.rwlabs.org
"""

import logging

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

API_URL = "https://api.reliefweb.int/v1/jobs"

# Categories most relevant to the profile
RELEVANT_CATEGORIES = [
    "Coordination",
    "Human Resources",
    "Administration",
    "Information Management",
    "Information and Communications Technology",
    "Programme and Project Management",
    "Donor Relations",
    "Monitoring and Evaluation",
    "Public Information",
    "Training",
    "Translation and Interpretation",
]


class ReliefWebProvider(BaseJobProvider):
    """Fetch humanitarian and NGO jobs from the ReliefWeb public API."""

    SOURCE_NAME = "reliefweb"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from ReliefWeb API. No API key required."""
        payload = {
            "appname": "swissjobhunter",
            "profile": "full",
            "preset": "latest",
            "limit": 100,
            "fields": {
                "include": [
                    "title",
                    "body",
                    "organization",
                    "url",
                    "date",
                    "country",
                    "city",
                    "career_categories",
                    "type",
                    "experience",
                    "language",
                    "how_to_apply",
                ]
            },
            "filter": {
                "operator": "AND",
                "conditions": [
                    {"field": "status", "value": "published"},
                ],
            },
            "sort": ["date.created:desc"],
        }

        # Filtra por query si se proporciona
        if query:
            payload["query"] = {"value": query, "operator": "AND"}

        async with httpx.AsyncClient() as client:
            data = await self._circuit.call(
                lambda: fetch_with_retry(
                    client,
                    API_URL,
                    method="POST",
                    json_body=payload,
                    timeout=20.0,
                )
            )

        if not data:
            return []

        raw_jobs = data.get("data", [])
        results = self._process_raw_jobs(raw_jobs)
        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a ReliefWeb API job entry into the unified schema."""
        fields = raw.get("fields", {})

        title = fields.get("title", "").strip()
        company = (fields.get("organization", {}) or {}).get("name", "").strip()

        # URL: preferir el enlace de ReliefWeb, luego el original
        url = fields.get("url", "") or f"https://reliefweb.int/job/{raw.get('id', '')}"

        # Descripción: combinar body + how_to_apply
        body = strip_html_tags(fields.get("body", "") or "")
        how = strip_html_tags(fields.get("how_to_apply", "") or "")
        description = body
        if how:
            description = f"{body}\n\nHow to apply: {how}".strip()

        # Ubicación: ciudad + país
        city = (fields.get("city") or [{}])[0].get("name", "") if fields.get("city") else ""
        countries = fields.get("country", []) or []
        country = countries[0].get("name", "") if countries else ""
        location_str = ", ".join(filter(None, [city, country])) or "Remote / Worldwide"

        # Idioma del job (primera entrada si existe)
        languages = fields.get("language", []) or []
        lang_name = languages[0].get("name", "").lower() if languages else None
        lang_code = _lang_name_to_code(lang_name)

        # Categorías como tags
        categories = fields.get("career_categories", []) or []
        category_tags = [c.get("name", "") for c in categories if c.get("name")]
        extracted_tags = extract_job_skills(title, description)
        tags = list(dict.fromkeys(category_tags + extracted_tags))[: self.MAX_TAGS]

        # Tipo de contrato
        job_type = (fields.get("type", []) or [{}])[0].get("name", "") if fields.get("type") else ""
        contract_type = _map_contract_type(job_type)

        # Fecha
        date_info = fields.get("date", {}) or {}
        _ = date_info.get("created", "")  # informativo, no usado en schema

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location_str,
            "canton": extract_canton(location_str),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": "remote" in location_str.lower() or not country,
            "tags": tags,
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": lang_code,
            "seniority": None,
            "contract_type": contract_type,
            "employment_type": job_type or None,
        }


def _lang_name_to_code(name: str | None) -> str | None:
    """Convert ReliefWeb language name to ISO 639-1 code."""
    if not name:
        return None
    _map = {
        "english": "en",
        "french": "fr",
        "spanish": "es",
        "arabic": "ar",
        "portuguese": "pt",
        "russian": "ru",
        "german": "de",
    }
    return _map.get(name.lower())


def _map_contract_type(job_type: str) -> str | None:
    """Map ReliefWeb type string to unified contract_type enum value."""
    if not job_type:
        return None
    t = job_type.lower()
    if "internship" in t or "intern" in t:
        return "internship"
    if "volunteer" in t:
        return "contract"
    if "fixed" in t or "temporary" in t:
        return "temporary"
    if "consultancy" in t or "consultant" in t or "contract" in t:
        return "contract"
    return "full_time"
