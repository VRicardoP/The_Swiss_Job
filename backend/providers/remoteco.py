"""Provider for Remote.co jobs (WordPress RSS feed).

Remote.co is a curated remote job board with strong coverage of
non-tech roles: customer service, writing, marketing, HR, education,
data entry, and transcription — all relevant for Alicia's profile.

RSS: https://remote.co/remote-jobs/feed/
"""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils import fetch_diagnostics as diag
from utils.http import fetch_rss
from utils.text import extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

RSS_URL = "https://remote.co/remote-jobs/feed/"

_TECH_EXCLUDE_TITLES = {
    "software engineer",
    "backend engineer",
    "frontend engineer",
    "full stack",
    "fullstack",
    "devops",
    "sre",
    "ml engineer",
    "data engineer",
    "cloud engineer",
    "mobile developer",
    "ios developer",
    "android developer",
    "blockchain",
    "cybersecurity",
    "security engineer",
    "embedded",
    "firmware",
    "network engineer",
    "infrastructure engineer",
}


class RemoteCoProvider(BaseJobProvider):
    """Fetch non-tech remote jobs from Remote.co RSS feed."""

    SOURCE_NAME = "remoteco"

    def _record_structure_failure(self, detail: str) -> None:
        """Registra un fallo de estructura como error de fetch VISIBLE.

        G3/P2-6: un HTTP 200 cuyo cuerpo no podemos leer NO es "no hay
        ofertas". Sin este registro el veredicto del run salía `empty` (sequía
        legítima) en vez de `error` y el panel de salud daba por sana una
        fuente rota. Mismo patrón que zebis (clase V.0/VD.7).
        """
        logger.error("remoteco: %s", detail)
        diag.record(diag.KIND_NETWORK, RSS_URL, detail=detail)

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from Remote.co RSS."""
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
        filtered = [
            j
            for j in all_jobs
            if not any(kw in j.get("title", "").lower() for kw in _TECH_EXCLUDE_TITLES)
        ]

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

        # Formato habitual: "Role — Company" o "Role at Company"
        company = ""
        if " at " in title:
            parts = title.rsplit(" at ", 1)
            title, company = parts[0].strip(), parts[1].strip()
        elif " — " in title:
            parts = title.split(" — ", 1)
            title, company = parts[0].strip(), parts[1].strip()

        category = (item.findtext("category") or "").strip()
        tags = extract_job_skills(title, description)
        if category and category.lower() not in [t.lower() for t in tags]:
            tags = [category] + tags

        return {
            "hash": self.compute_hash(title, company, url or guid),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": "Remote / Worldwide",
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
            "language": "en",
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }
