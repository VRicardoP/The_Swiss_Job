"""Provider for Jooble job aggregator API."""

import asyncio
import json
import logging

import httpx

from config import settings
from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider
from utils import fetch_diagnostics as diag
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

# La URL real incrusta la API key en el path: para los diagnósticos (que se
# muestran en el panel de salud) se usa esta forma redactada.
_REDACTED_URL = "https://jooble.org/api/<api_key>"


class JoobleProvider(BaseJobProvider):
    """Fetch jobs from the Jooble API (POST-based, requires API key)."""

    SOURCE_NAME = "jooble"
    API_URL_TEMPLATE = "https://jooble.org/api/{api_key}"
    MAX_PAGES = 3

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from Jooble, paginating up to MAX_PAGES."""
        api_key = settings.JOOBLE_API_KEY
        if not api_key:
            logger.warning("Jooble API key not configured, skipping provider")
            return []

        api_url = self.API_URL_TEMPLATE.format(api_key=api_key)
        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for page in range(1, self.MAX_PAGES + 1):
                json_body = {
                    "keywords": query,
                    "location": location,
                    "page": str(page),
                }

                try:
                    data = await self._circuit.call(
                        lambda body=json_body: fetch_with_retry(
                            client,
                            api_url,
                            method="POST",
                            json_body=body,
                        )
                    )
                except (CircuitBreakerOpen, httpx.HTTPError, json.JSONDecodeError) as e:
                    logger.error("Jooble fetch error on page %d: %s", page, e)
                    break

                # G4/P2-8: un 200 ilegible (cuerpo vacío, clave renombrada) ya no
                # se confunde con "no hay ofertas" — se registra y la fuente sale
                # `error`, no `empty`.
                # OJO: la url lleva la API key incrustada y `diag` acaba en la
                # columna de salud, que el panel muestra — se registra redactada.
                raw_jobs = diag.json_items(
                    data, _REDACTED_URL, self.SOURCE_NAME, key="jobs"
                )
                if not raw_jobs:
                    break

                results.extend(self._process_raw_jobs(raw_jobs))

                # Check if there are more results.
                # G3/P3-11: casteo seguro (residual del fix G1/P3-3, aplicado
                # entonces a careerjet.pages y jobgether.maxPages). Un
                # totalCount string daba TypeError que ESCAPABA de fetch_jobs
                # —el except de arriba no lo cubre— y perdía la fuente entera;
                # ausente o 0 cortaba en la página 1 en silencio. El 0 es
                # falsy: se sigue paginando hasta el tope o la página vacía.
                total_count = self._safe_int(data.get("totalCount"))
                if total_count and len(results) >= total_count:
                    break

                # Delay between pages
                if page < self.MAX_PAGES:
                    await asyncio.sleep(0.5)

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Jooble API response into the unified job schema."""
        title = (raw.get("title") or "").strip()
        company = (raw.get("company") or "").strip()
        url = (raw.get("link") or "").strip()
        snippet = raw.get("snippet", "")
        description = strip_html_tags(snippet)
        location_raw = (raw.get("location") or "").strip()
        employment_type = raw.get("type") or None
        salary_raw = raw.get("salary") or ""

        tags = extract_job_skills(title, description)

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location_raw if location_raw else "Switzerland",
            "canton": extract_canton(location_raw),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": False,
            "tags": tags,
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": salary_raw if salary_raw else None,
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": employment_type,
        }
