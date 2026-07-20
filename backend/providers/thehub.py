"""Provider for The Hub (thehub.io) startup jobs API."""

import asyncio
import logging

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

PAGE_DELAY_SECONDS = 0.5


class TheHubProvider(BaseJobProvider):
    """Fetch remote jobs from The Hub (thehub.io) public API.

    La API no requiere auth ni gating de User-Agent. Devuelve las ofertas en
    `response["docs"]` (15 por página), con la paginación en la raíz
    (`total`, `limit`, `page`, `pages`). Filtramos por `isRemote=true`.
    """

    SOURCE_NAME = "thehub"
    API_URL = "https://thehub.io/api/jobs"
    # Los logos se sirven desde el CDN imgix, no desde thehub.io (allí dan 404).
    LOGO_BASE = "https://thehub-io.imgix.net"
    MAX_PAGES = 5

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from The Hub, paginating hasta MAX_PAGES."""
        results: list[dict] = []
        max_pages = self._pages_budget()

        async with httpx.AsyncClient() as client:
            for page in range(1, max_pages + 1):
                # default arg `p=page` captura el valor y evita late-binding en el lambda
                data = await self._circuit.call(
                    lambda p=page: fetch_with_retry(
                        client,
                        self.API_URL,
                        params={"isRemote": "true", "page": p},
                    )
                )

                if not data:
                    break

                raw_jobs = data.get("docs", [])
                if not raw_jobs:
                    break

                results.extend(self._process_raw_jobs(raw_jobs))

                # `pages` es el total de páginas; parar al alcanzar la última.
                total_pages = self._safe_int(data.get("pages"))
                if total_pages and page >= total_pages:
                    break

                if page < max_pages:
                    await asyncio.sleep(PAGE_DELAY_SECONDS)

        return self._finalize_fetch(results)

    @staticmethod
    def _safe_int(value: object) -> int:
        """Castea `pages`/`page` (a veces string, p.ej. \"4\") a int. 0 si no procede."""
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    def _logo_url(self, company_data: dict) -> str | None:
        """URL del logo desde el CDN imgix, o None si la oferta no trae logoImage."""
        logo_image = company_data.get("logoImage") or {}
        path = (logo_image.get("path") or "").strip()
        return f"{self.LOGO_BASE}{path}" if path else None

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw The Hub API response into the unified job schema."""
        title = (raw.get("title") or "").strip()

        company_data = raw.get("company") or {}
        company = (company_data.get("name") or "").strip()

        url = (raw.get("absoluteJobUrl") or "").strip()
        description = strip_html_tags(raw.get("description") or "")

        # location suele venir {} → toleramos ausencia (location vacío, canton None).
        location_data = raw.get("location") or {}
        location_str = (
            location_data.get("address") or location_data.get("locality") or ""
        ).strip()

        # remote es booleano ESTRUCTURAL (campo real), no heurística de título.
        is_remote = bool(raw.get("isRemote", False))

        # El salario de The Hub solo llega como texto libre ("competitive",
        # "unpaid") y los *Range son objetos vacíos → sin dato numérico fiable.
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
            "remote": is_remote,
            "tags": extract_job_skills(title, description)[: self.MAX_TAGS],
            "logo": self._logo_url(company_data),
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }
