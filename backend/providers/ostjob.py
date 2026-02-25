"""Provider for Ostjob (CH Media Eastern Switzerland job portal)."""

import asyncio
import json
import logging

import httpx

from providers.base_chmedia import normalize_chmedia_job
from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry

logger = logging.getLogger(__name__)


class OstjobProvider(BaseJobProvider):
    """Fetch jobs from the Ostjob CH Media API."""

    SOURCE_NAME = "ostjob"
    DOMAIN = "ostjob.ch"
    API_URL = "https://api.ostjob.ch/public/vacancy/search/"
    MAX_PAGES = 10
    PAGE_SIZE = 20

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from Ostjob, paginating up to MAX_PAGES."""
        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for page in range(1, self.MAX_PAGES + 1):
                params = {"page": page, "size": self.PAGE_SIZE}

                try:
                    data = await self._circuit.call(
                        lambda p=params: fetch_with_retry(
                            client,
                            self.API_URL,
                            params=p,
                            headers=self.DEFAULT_HEADERS,
                        )
                    )
                except (CircuitBreakerOpen, httpx.HTTPError, json.JSONDecodeError) as e:
                    logger.error("Ostjob fetch error on page %d: %s", page, e)
                    break

                if not data:
                    break

                raw_jobs = data.get("items", [])
                if not raw_jobs:
                    break

                results.extend(self._process_raw_jobs(raw_jobs))

                # Delay between pages to avoid rate limiting
                if page < self.MAX_PAGES and len(raw_jobs) == self.PAGE_SIZE:
                    await asyncio.sleep(0.5)
                else:
                    # Last page had fewer results than page size — no more pages
                    break

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Ostjob API response into the unified job schema."""
        return normalize_chmedia_job(raw, self.SOURCE_NAME, self.DOMAIN)
