"""Scraper para el portal Workday del grupo International Schools Partnership.

Cubre 1 colegio de la watchlist:
- Mosaic Ecole (Geneva)

Estrategia:
- Workday expone una API JSON pública en /wday/cxs/{tenant}/{site}/jobs
- POST con cuerpo {searchText, limit, offset} devuelve lista paginada
- Filtramos por nombre del colegio en locationsText (ej. "Mosaic School")
- Categoría fijada a "A" para saltarse la penalización H
"""

import logging

import httpx

from scrapers.swiss_schools_config import WatchedSchool, schools_by_strategy
from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider

logger = logging.getLogger(__name__)


class SwissSchoolsISPScraper(BaseJobProvider):
    """Workday API directa — no usa el flujo HTML del BaseScraper."""

    SOURCE_NAME = "swiss_schools_isp"
    PAGE_SIZE = 20
    MAX_PAGES = 10

    def __init__(self):
        super().__init__()
        self._schools: list[WatchedSchool] = schools_by_strategy("isp_workday")

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        all_raw: list[dict] = []
        async with httpx.AsyncClient(timeout=20.0) as client:
            for school in self._schools:
                params = school.params or {}
                tenant = params.get("tenant", "")
                site = params.get("site", "")
                school_filter = params.get("school_filter", "").lower()
                if not tenant or not site:
                    continue

                raw_jobs = await self._fetch_workday(
                    client, tenant, site, school_filter
                )
                for r in raw_jobs:
                    r["_school"] = school
                    all_raw.append(r)

        results = self._process_raw_jobs(all_raw)
        return self._finalize_fetch(results)

    async def _fetch_workday(
        self,
        client: httpx.AsyncClient,
        tenant: str,
        site: str,
        school_filter: str,
    ) -> list[dict]:
        api_url = (
            f"https://{tenant}.wd3.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
        )
        results: list[dict] = []

        for page in range(self.MAX_PAGES):
            offset = page * self.PAGE_SIZE
            payload = {
                "appliedFacets": {},
                "limit": self.PAGE_SIZE,
                "offset": offset,
                "searchText": school_filter,
            }
            try:
                resp = await self._circuit.call(
                    lambda: client.post(api_url, json=payload)
                )
            except (CircuitBreakerOpen, httpx.HTTPError) as e:
                logger.error("ISP Workday fetch error: %s", e)
                break

            if resp.status_code != 200:
                logger.warning("ISP Workday HTTP %d", resp.status_code)
                break

            data = resp.json()
            postings = data.get("jobPostings", [])

            # Filtro estricto por locationsText (ej. "Mosaic School / Ecole Mosaic")
            for p in postings:
                location_text = (p.get("locationsText") or "").lower()
                if school_filter in location_text:
                    results.append(p)

            if len(postings) < self.PAGE_SIZE:
                break

        return results

    def normalize_job(self, raw: dict) -> dict:
        school: WatchedSchool = raw["_school"]
        external_path = raw.get("externalPath", "")
        # Detail URL: base_career_site + externalPath
        params = school.params or {}
        tenant = params.get("tenant", "")
        site = params.get("site", "")
        base = f"https://{tenant}.wd3.myworkdayjobs.com/en-US/{site}"
        url = f"{base}{external_path}"

        # Source ID: JR... de bulletFields
        bullet = raw.get("bulletFields") or []
        source_id = bullet[0] if bullet else external_path

        title = raw.get("title", "")
        location_text = raw.get("locationsText", "")

        job = {
            "source": self.SOURCE_NAME,
            "source_id": source_id,
            "title": title,
            "company": school.name,
            "location": location_text or f"{school.city}, CH",
            "url": url,
            # Categoría real la asigna el classifier; bypass en match_service.
            "tags": ["education", "international school", school.id],
            "language": "en",
        }
        job["hash"] = self.compute_hash(title, school.name, url)
        return job
