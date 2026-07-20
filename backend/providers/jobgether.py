"""Provider for the Jobgether remote/flexible jobs search API."""

import asyncio
import json
import logging

import httpx

from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider
from services.scraper_stealth import realistic_headers
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills

logger = logging.getLogger(__name__)

# Valores de `remoteOfferType` que NO son remoto (en minúsculas). Hoy la API solo
# devuelve "Full Remote"; esta lista evita falsos positivos si aparecieran otros.
_NON_REMOTE_TYPES = frozenset(
    {"no remote", "not remote", "on-site", "onsite", "office"}
)


class JobgetherProvider(BaseJobProvider):
    """Fetch jobs from the Jobgether search API (paginated, remote-focused).

    La API devuelve `Content-Type: text/plain` pero cuerpo JSON; httpx lo parsea
    igualmente en `response.json()` (no valida el content-type), así que
    `fetch_with_retry` sirve tal cual. Sin cabecera User-Agent de navegador la
    API responde 403 (anti-bot): por eso enviamos `realistic_headers()`.
    """

    SOURCE_NAME = "jobgether"
    API_URL = "https://jobgether.com/astroapi/offer/search"
    # El slug ya incluye el id de la oferta; la URL pública es base + slug.
    OFFER_URL_BASE = "https://jobgether.com/offer/"
    MAX_PAGES = 3
    PAGE_DELAY_SECONDS = 0.5

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from Jobgether, paginando hasta MAX_PAGES o `maxPages`."""
        headers = realistic_headers()
        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for page in range(1, self.MAX_PAGES + 1):
                params = {"keyword": query, "page": page}
                try:
                    data = await self._circuit.call(
                        lambda p=params: fetch_with_retry(
                            client, self.API_URL, params=p, headers=headers
                        )
                    )
                except (CircuitBreakerOpen, httpx.HTTPError, json.JSONDecodeError) as e:
                    logger.error("Jobgether fetch error on page %d: %s", page, e)
                    break

                if not data:
                    break

                raw_jobs = data.get("data", [])
                if not raw_jobs:
                    break

                results.extend(self._process_raw_jobs(raw_jobs))

                # Terminación: no pedir más allá de la última página real.
                max_pages = data.get("maxPages", 1)
                if page >= max_pages:
                    break

                # Retardo cortés entre páginas para no martillear la API.
                if page < self.MAX_PAGES:
                    await asyncio.sleep(self.PAGE_DELAY_SECONDS)

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transforma una oferta cruda de Jobgether al esquema unificado."""
        title = (raw.get("title") or "").strip()

        company_data = raw.get("companyData") or {}
        company = (company_data.get("name") or "").strip()
        logo = (company_data.get("logo") or "").strip() or None

        # El slug incluye el id; sin slug no hay URL → la oferta se descarta.
        slug = (raw.get("slug") or "").strip()
        url = f"{self.OFFER_URL_BASE}{slug}" if slug else ""

        location_raw = (raw.get("requiredLocations") or "").strip()

        # remote: campo ESTRUCTURAL (remoteOfferType, p.ej. "Full Remote"), no
        # heurística de título. contractType es cosa distinta (employment_type).
        # Guard explícito: un valor no vacío que anuncie NO-remoto (on-site) no debe
        # marcarse remote=True. Hoy la API solo devuelve "Full Remote", pero así el
        # provider no da falsos positivos si aparecen "No Remote"/"On-site".
        remote_type = (raw.get("remoteOfferType") or "").strip().lower()
        remote = bool(remote_type) and remote_type not in _NON_REMOTE_TYPES
        employment_type = (raw.get("contractType") or "").strip() or None

        tags = self._build_tags(raw, title)
        salary_original, salary_currency = self._parse_salary(raw)

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location_raw,
            "canton": extract_canton(location_raw),
            # El listado no trae descripción y no hacemos 2ª llamada por oferta.
            "description": "",
            "description_snippet": None,
            "url": url,
            "remote": remote,
            "tags": tags,
            "logo": logo,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": salary_original,
            "salary_currency": salary_currency,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": employment_type,
        }

    def _build_tags(self, raw: dict, title: str) -> list[str]:
        """Combina los skills declarados por la API con los extraídos del título."""
        api_skills = [
            (s.get("name") or "").strip()
            for s in (raw.get("skills") or [])
            if isinstance(s, dict) and s.get("name")
        ]
        extracted = extract_job_skills(title, "")

        seen_lower: set[str] = set()
        merged: list[str] = []
        for tag in api_skills + extracted:
            if tag and tag.lower() not in seen_lower:
                seen_lower.add(tag.lower())
                merged.append(tag)
        return merged[: self.MAX_TAGS]

    def _parse_salary(self, raw: dict) -> tuple[str | None, str | None]:
        """Devuelve (salary_original, salary_currency).

        Presencia = `salary.average > 0`. Los importes NO son CHF (USD/CAD/…), así
        que van a `salary_original`/`salary_currency`; la conversión a CHF se hace
        aguas abajo (por eso `salary_*_chf` quedan a None, como el resto de
        providers). Nunca devolvemos strings tipo "Competitive".
        """
        salary = raw.get("salary") or {}
        if not salary.get("average", 0) or salary.get("average", 0) <= 0:
            return None, None

        currency = (salary.get("currency") or "").strip() or None
        parts: list[str] = []
        for key in ("min", "max"):
            value = salary.get(key)
            if value:
                parts.append(str(int(value)))
        if not parts:
            return None, currency

        amount = "-".join(parts)
        original = f"{amount} {currency}".strip() if currency else amount
        return original, currency
