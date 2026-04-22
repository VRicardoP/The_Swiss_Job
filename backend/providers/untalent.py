"""Provider for UN Talent public API (United Nations jobs aggregator).

UN Talent (untalent.org) aggregates jobs from the entire UN system:
UNICEF, ILO, UNOG, UNDP, UNESCO, WHO, WFP, UNHCR, and 40+ agencies.
The public JSON API requires no authentication for fair use.

API: https://untalent.org/api/v1/jobs?page=1
API docs: https://github.com/UNTalent/Documentation
"""

import logging

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

API_URL = "https://untalent.org/api/v1/jobs"
HOME_BASED_URL = "https://untalent.org/api/v1/homebased"

# Número máximo de páginas a recuperar (cada página ~20 jobs)
_MAX_PAGES = 5

# Áreas relevantes para el perfil de Alicia (slugs de la API)
_RELEVANT_AREA_SLUGS = [
    "human-resources",
    "administration",
    "public-information",
    "translation-and-interpretation",
    "programme-and-project-management",
    "information-management",
    "coordination",
    "training",
    "conference-services",
    "communications",
    "education",
    "documentation",
]


class UNTalentProvider(BaseJobProvider):
    """Fetch UN system jobs from UN Talent public JSON API.

    Cubre toda la familia ONU: UNICEF, ILO, UNOG, UNDP, UNESCO,
    WHO, WFP, UNHCR y más de 40 agencias adicionales.
    """

    SOURCE_NAME = "untalent"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch UN jobs: home-based jobs first, then general listing."""
        jobs: list[dict] = []

        async with httpx.AsyncClient() as client:
            # Paso 1: trabajos home-based (mayor relevancia para Alicia)
            home_jobs = await self._fetch_pages(client, HOME_BASED_URL, pages=_MAX_PAGES)
            jobs.extend(home_jobs)

            # Paso 2: todos los trabajos (primera página, para no duplicar demasiado)
            all_jobs = await self._fetch_pages(client, API_URL, pages=2)
            jobs.extend(all_jobs)

        if not jobs:
            return []

        # Procesar y deduplicar por URL antes de pasar al pipeline
        seen_urls: set[str] = set()
        unique_raw: list[dict] = []
        for job in jobs:
            url = (job.get("url") or "").strip()
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_raw.append(job)

        normalized = self._process_raw_jobs(unique_raw)

        if query:
            q_lower = query.lower()
            normalized = [
                j for j in normalized
                if q_lower in f"{j['title']} {j['description']}".lower()
            ]

        return self._finalize_fetch(normalized)

    async def _fetch_pages(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        pages: int,
    ) -> list[dict]:
        """Itera sobre páginas de la API y acumula los items."""
        all_items: list[dict] = []
        for page in range(1, pages + 1):
            try:
                data = await self._circuit.call(
                    lambda p=page: fetch_with_retry(
                        client,
                        f"{base_url}?page={p}",
                        timeout=20.0,
                    )
                )
            except Exception as exc:
                logger.warning("UNTalent page %d fetch error: %s", page, exc)
                break

            if not data:
                break

            # La API devuelve directamente una lista o un dict con "jobs"
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("jobs") or data.get("data") or data.get("results") or []
            else:
                break

            if not items:
                break  # Sin más páginas

            all_items.extend(items)

        return all_items

    def normalize_job(self, raw: dict) -> dict:
        """Transform a UN Talent API entry into the unified job schema."""
        title = (raw.get("title") or "").strip()
        company = (raw.get("company") or raw.get("organization") or "").strip()
        url = (raw.get("url") or raw.get("link") or "").strip()
        description_html = raw.get("description") or raw.get("body") or ""
        description = strip_html_tags(str(description_html)) if description_html else ""

        # Ubicación: puede venir como string o lista
        location_raw = raw.get("location") or raw.get("city") or ""
        if isinstance(location_raw, list):
            location_str = ", ".join(str(x) for x in location_raw if x)
        else:
            location_str = str(location_raw).strip()
        location_str = location_str or "International"

        # Nivel / seniority
        level = (raw.get("level") or raw.get("grade") or "").lower()
        seniority = _map_seniority(level)

        # Tags: extraer de título + descripción
        area = raw.get("area") or raw.get("category") or ""
        tags = extract_job_skills(title, description)
        if area and str(area).lower() not in [t.lower() for t in tags]:
            tags = [str(area)] + tags

        is_remote = (
            "home-based" in location_str.lower()
            or "remote" in location_str.lower()
            or "homebased" in raw.get("type", "").lower()
        )

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
            "tags": tags[: self.MAX_TAGS],
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": "en",
            "seniority": seniority,
            "contract_type": _map_contract(raw.get("contract") or raw.get("type") or ""),
            "employment_type": None,
        }


def _map_seniority(level: str) -> str | None:
    """Mapea nivel ONU a seniority unificado."""
    if not level:
        return None
    if any(x in level for x in ["p-1", "p1", "g-1", "g1", "g-2", "g2", "junior"]):
        return "junior"
    if any(x in level for x in ["p-2", "p2", "p-3", "p3", "g-3", "g3", "g-4", "g4", "mid"]):
        return "mid"
    if any(x in level for x in ["p-4", "p4", "p-5", "p5", "senior"]):
        return "senior"
    if any(x in level for x in ["d-1", "d1", "d-2", "d2", "lead", "chief", "head"]):
        return "lead"
    return None


def _map_contract(contract: str) -> str | None:
    """Mapea tipo de contrato ONU a enum unificado."""
    if not contract:
        return None
    c = contract.lower()
    if "temporary" in c or "temp" in c or "fixed" in c:
        return "temporary"
    if "consultant" in c or "consultancy" in c or "individual contract" in c:
        return "contract"
    if "internship" in c or "intern" in c:
        return "internship"
    if "permanent" in c or "continuing" in c or "regular" in c:
        return "full_time"
    return None
