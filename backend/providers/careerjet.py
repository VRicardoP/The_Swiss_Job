"""Provider for Careerjet job search API."""

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


class CareerjetProvider(BaseJobProvider):
    """Fetch jobs from the Careerjet public search API."""

    SOURCE_NAME = "careerjet"
    API_URL = "https://public.api.careerjet.net/search"
    MAX_PAGES = 3
    PAGE_SIZE = 50

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from Careerjet, paginating up to MAX_PAGES."""
        affid = settings.CAREERJET_AFFID
        if not affid:
            logger.warning("Careerjet affiliate ID not configured, skipping provider")
            return []

        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for page in range(1, self.MAX_PAGES + 1):
                params = {
                    "affid": affid,
                    "user_ip": "1.0.0.1",
                    "user_agent": self.USER_AGENT,
                    "locale_code": "en",
                    "keywords": query,
                    "location": location,
                    "page": page,
                    "pagesize": self.PAGE_SIZE,
                    "sort": "date",
                }

                try:
                    data = await self._circuit.call(
                        lambda p=params: fetch_with_retry(
                            client, self.API_URL, params=p
                        )
                    )
                except (CircuitBreakerOpen, httpx.HTTPError, json.JSONDecodeError) as e:
                    logger.error("Careerjet fetch error on page %d: %s", page, e)
                    break

                if data is None:
                    # Fetch fallido: el issue ya lo registró utils.http.
                    break

                # G4/P2-8: las dos guardas de estructura de G3/P2-6 estaban
                # DETRÁS de un `if not data: break`, así que un 200 con `{}` o
                # `[]` las esquivaba y la fuente volvía a salir `empty`. El
                # corte por falsy se sustituye por el corte por None (que es lo
                # único que utils.http ya ha registrado) y las guardas se
                # evalúan siempre.
                if not isinstance(data, dict):
                    detail = (
                        "200 con estructura desconocida: se esperaba un objeto "
                        f"y llegó {type(data).__name__}"
                    )
                    logger.error("Careerjet: %s", detail)
                    diag.record(diag.KIND_NETWORK, self.API_URL, detail=detail)
                    break

                # Verify response type. G3/P2-6: la API responde HTTP 200 con
                # {"type": "ERROR", ...} cuando el affid es inválido o está
                # revocado. Sin registrar el fallo, el día que caduque
                # CAREERJET_AFFID la fuente muere en silencio y el panel de
                # salud la da por sana (clase V.0).
                resp_type = data.get("type", "")
                if resp_type != "JOBS":
                    detail = (
                        f"respuesta type={resp_type or 'ausente'} (se esperaba JOBS)"
                    )
                    logger.error("Careerjet: %s", detail)
                    diag.record(diag.KIND_NETWORK, self.API_URL, detail=detail)
                    break

                # G3/P2-6: `jobs` AUSENTE es estructura desconocida; `jobs`
                # presente y vacío sí es fin de paginación / sin resultados
                # legítimo (una búsqueda sin hits devuelve type=JOBS, jobs=[]).
                if "jobs" not in data:
                    detail = (
                        "respuesta JOBS sin la clave 'jobs': estructura desconocida"
                    )
                    logger.error("Careerjet: %s", detail)
                    diag.record(diag.KIND_NETWORK, self.API_URL, detail=detail)
                    break

                raw_jobs = data.get("jobs") or []
                if not raw_jobs:
                    break

                results.extend(self._process_raw_jobs(raw_jobs))

                # Check if we've reached the last page
                # G1/P3-3: casteo seguro (ver jobgether) — sin TypeError con un
                # `pages` string ni corte silencioso si el campo desaparece.
                total_pages = self._safe_int(data.get("pages"))
                if total_pages and page >= total_pages:
                    break

                # Delay between pages to avoid rate limiting
                if page < self.MAX_PAGES:
                    await asyncio.sleep(0.5)

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform a raw Careerjet API response into the unified job schema."""
        title = (raw.get("title") or "").strip()
        company = (raw.get("company") or "").strip()
        url = (raw.get("url") or "").strip()
        description_html = raw.get("description", "")
        description = strip_html_tags(description_html)
        location_raw = (raw.get("locations") or "").strip()
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
            "employment_type": None,
        }
