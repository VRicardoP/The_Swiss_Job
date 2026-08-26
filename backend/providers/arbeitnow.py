"""Provider for Arbeitnow job board API."""

import asyncio
import logging
import re

import httpx

from services.job_service import BaseJobProvider
from utils.dates import parse_published_at
from utils import fetch_diagnostics as diag
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

MAX_PAGES = 3
PAGE_DELAY_SECONDS = 0.5

# G3/P3-13: el id numérico final de la URL cambia cada vez que el portal
# reemite la MISMA vacante ("…-stuttgart-459633" y "…-stuttgart-198909" son la
# misma oferta), así que la identidad era volátil y cada reemisión creaba una
# fila nueva. El dedup semántico excluye a propósito los pares de la misma
# fuente, de modo que nadie los recogía después.
_VOLATILE_ID_SUFFIX = re.compile(r"-\d+/?$")


def canonical_identity_url(url: str) -> str:
    """URL sin el id volátil final, para computar una identidad ESTABLE.

    Solo se usa para el `hash`: la `url` publicada sigue siendo la real y
    `ON CONFLICT (hash)` la refresca, de modo que la reemisión pasa a ser una
    re-vista de la oferta existente en vez de un clon.
    """
    return _VOLATILE_ID_SUFFIX.sub("", url.strip())


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

                # G4/P2-8: un 200 ilegible (cuerpo vacío, clave renombrada) ya no
                # se confunde con "no hay ofertas" — se registra y la fuente sale
                # `error`, no `empty`.
                raw_jobs = diag.json_items(
                    data, self.API_URL, self.SOURCE_NAME, key="data"
                )
                if not raw_jobs:
                    break

                results.extend(self._process_raw_jobs(raw_jobs))

                # Polite delay between pages
                if page < MAX_PAGES:
                    await asyncio.sleep(PAGE_DELAY_SECONDS)

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Arbeitnow API response into the unified job schema."""
        title = (raw.get("title") or "").strip()
        company = (raw.get("company_name") or "").strip()
        url = (raw.get("url") or "").strip()
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
            "hash": self.compute_hash(title, company, canonical_identity_url(url)),
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
            # Fecha del PORTAL (created_at, epoch en segundos).
            "published_at": parse_published_at(raw.get("created_at")),
        }
