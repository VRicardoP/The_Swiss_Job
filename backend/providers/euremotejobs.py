"""Provider for EU Remote Jobs (RSS feed / scraper).

EU Remote Jobs aggregates remote positions for candidates in European
time zones (CET/CEST) — highly relevant for Alicia based in Spain.
Covers content, marketing, customer success, HR, and admin roles.

RSS feed: https://euremotejobs.com/feed/
"""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils.dates import parse_published_at
from utils import fetch_diagnostics as diag
from utils.http import fetch_rss
from utils.text import extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

RSS_URL = "https://euremotejobs.com/feed/"

# Palabras en título que indican roles técnicos a descartar
_TECH_EXCLUDE = {
    "software engineer",
    "backend engineer",
    "frontend engineer",
    "full stack",
    "fullstack",
    "devops",
    "sre",
    "site reliability",
    "ml engineer",
    "data engineer",
    "cloud engineer",
    "platform engineer",
    "mobile developer",
    "ios developer",
    "android developer",
    "blockchain",
    "cybersecurity",
    "security engineer",
    "embedded",
    "firmware",
    "hardware engineer",
    "network engineer",
}


class EURemoteJobsProvider(BaseJobProvider):
    """Fetch European remote jobs from EU Remote Jobs RSS feed."""

    SOURCE_NAME = "euremotejobs"

    def _record_structure_failure(self, detail: str) -> None:
        """Registra un fallo de estructura como error de fetch VISIBLE.

        G3/P2-6: un HTTP 200 cuyo cuerpo no podemos leer NO es "no hay
        ofertas". Sin este registro el veredicto del run salía `empty` (sequía
        legítima) en vez de `error` y el panel de salud daba por sana una
        fuente rota. Mismo patrón que zebis (clase V.0/VD.7).
        """
        logger.error("euremotejobs: %s", detail)
        diag.record(diag.KIND_NETWORK, RSS_URL, detail=detail)

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from EU Remote Jobs RSS."""
        async with httpx.AsyncClient() as client:
            xml_text = await self._circuit.call(
                lambda: fetch_rss(
                    client,
                    RSS_URL,
                    headers=self.DEFAULT_HEADERS,
                    timeout=20.0,
                )
            )

        # G3/P2-6: SOLO el None de fetch_rss corta aquí (fetch fallido cuyo
        # issue ya registró utils.http). Un "" —200 con cuerpo vacío— fluye a
        # ET.fromstring y sale como fallo de estructura, no como feed vacío.
        if xml_text is None:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            self._record_structure_failure(f"RSS XML ilegible: {exc}")
            return []

        channel = root.find("channel")
        if channel is None:
            self._record_structure_failure("RSS sin <channel>: estructura desconocida")
            return []

        items = channel.findall("item")
        all_jobs = self._process_raw_jobs(items)
        filtered = self._exclude_tech_roles(all_jobs)

        if query:
            q_lower = query.lower()
            filtered = [
                j
                for j in filtered
                if q_lower in f"{j['title']} {j['description']}".lower()
            ]

        return self._finalize_fetch(filtered)

    def normalize_job(self, raw: ET.Element) -> dict:
        """Transform an RSS <item> into the unified job schema."""
        item = raw

        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        description_html = item.findtext("description") or ""
        description = strip_html_tags(description_html)
        category = (item.findtext("category") or "").strip()

        # Empresa: WordPress posts suelen tener "Role at Company" o "Company: Role"
        company = ""
        if " at " in title:
            parts = title.rsplit(" at ", 1)
            title, company = parts[0].strip(), parts[1].strip()
        elif ": " in title and len(title.split(": ", 1)[0]) < 50:
            parts = title.split(": ", 1)
            company, title = parts[0].strip(), parts[1].strip()

        tags = extract_job_skills(title, description)
        if category and category.lower() not in [t.lower() for t in tags]:
            tags = [category] + tags

        return {
            "hash": self.compute_hash(title, company, url or guid),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": "Remote / Europe",
            "canton": None,
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url or guid,
            "remote": True,
            "tags": tags[: self.MAX_TAGS],
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": "en",  # EU Remote Jobs opera en inglés
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
            # Fecha del PORTAL (pubDate RFC822 del feed WordPress).
            "published_at": parse_published_at(item.findtext("pubDate")),
        }

    @staticmethod
    def _exclude_tech_roles(jobs: list[dict]) -> list[dict]:
        """Filtra roles puramente técnicos para reducir ruido."""
        result = []
        for job in jobs:
            title_lower = job.get("title", "").lower()
            if any(kw in title_lower for kw in _TECH_EXCLUDE):
                continue
            result.append(job)
        return result
