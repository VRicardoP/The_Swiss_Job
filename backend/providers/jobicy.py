"""Provider for Jobicy remote jobs API."""

import logging

import httpx

from services.job_service import BaseJobProvider
from utils.dates import parse_published_at
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)


class JobicyProvider(BaseJobProvider):
    """Fetch remote jobs from the Jobicy API."""

    SOURCE_NAME = "jobicy"
    API_URL = "https://jobicy.com/api/v2/remote-jobs"
    # Sin query (la cosecha llama con ""), además del feed genérico se piden
    # estos tags: el registry lo tenía desactivado como «tech-only» y ya no lo
    # es — la sonda 2026-09-02 dio 13/50 ofertas del nicho de los perfiles
    # reales (customer success/contenido/localización). Lista corta a
    # propósito: cada tag es UNA petición por run.
    DEFAULT_TAGS = (
        "customer-success",
        "copywriting",
        "technical-writing",
        "translation",
    )

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from Jobicy filtered by tag."""
        tags: tuple[str | None, ...] = (
            (query,) if query else (None,) + self.DEFAULT_TAGS
        )
        geo = location if location and location.lower() != "switzerland" else None

        vistos: set[str] = set()
        results: list[dict] = []
        async with httpx.AsyncClient() as client:
            for tag in tags:
                params: dict[str, str | int] = {"count": 50}
                if tag:
                    params["tag"] = tag
                if geo:
                    params["geo"] = geo
                data = await self._circuit.call(
                    lambda p=params: fetch_with_retry(client, self.API_URL, params=p)
                )
                # P2 revisión 2026-09-03: la forma externa se valida ANTES
                # del dedupe — una raíz lista, jobs=null o un elemento escalar
                # tumbaba las cinco consultas de la fuente con un AttributeError.
                if not isinstance(data, dict):
                    if data:
                        logger.warning(
                            "jobicy: raíz inesperada %s — lote descartado",
                            type(data).__name__,
                        )
                    continue
                raw_jobs = data.get("jobs")
                if not isinstance(raw_jobs, list):
                    logger.warning(
                        "jobicy: 'jobs' no es lista (%s) — lote descartado",
                        type(raw_jobs).__name__,
                    )
                    continue
                validos = []
                for r in raw_jobs:
                    if not isinstance(r, dict):
                        logger.warning(
                            "jobicy: elemento no-objeto descartado: %r",
                            r if not isinstance(r, (bytes, str)) else str(r)[:60],
                        )
                        continue
                    validos.append(r)
                # dedupe entre tags por URL (la misma oferta sale en varios)
                nuevos = [r for r in validos if (r.get("url") or "") not in vistos]
                vistos.update((r.get("url") or "") for r in nuevos)
                results.extend(self._process_raw_jobs(nuevos))

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Jobicy API response into the unified job schema."""
        title = (raw.get("jobTitle") or "").strip()
        company = (raw.get("companyName") or "").strip()
        url = (raw.get("url") or "").strip()
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
            # Fecha del PORTAL (pubDate, ISO8601 con offset).
            "published_at": parse_published_at(raw.get("pubDate")),
        }
