"""Provider for Impactpool jobs (RSS feed).

Impactpool is the leading job portal for international organisations,
UN agencies, NGOs, and development sector — the best source for
Category F roles in Alicia's profile (organismos internacionales).

RSS feed: https://www.impactpool.org/feeds/jobs.rss
"""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

RSS_URL = "https://www.impactpool.org/feeds/jobs.rss"

# Palabras clave en título que indican roles relevantes para Alicia
_RELEVANT_TITLE_KEYWORDS = {
    "assistant",
    "coordinator",
    "officer",
    "specialist",
    "associate",
    "analyst",
    "consultant",
    "advisor",
    "manager",
    "director",
    "intern",
    "communications",
    "hr",
    "human resources",
    "admin",
    "programme",
    "project",
    "monitoring",
    "evaluation",
    "information",
    "translation",
    "interpreter",
    "editor",
    "content",
    "training",
    "learning",
}


class ImpactPoolProvider(BaseJobProvider):
    """Fetch international organisation jobs from Impactpool RSS feed."""

    SOURCE_NAME = "impactpool"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from Impactpool RSS."""
        async with httpx.AsyncClient() as client:
            xml_text = await self._circuit.call(
                lambda: fetch_rss(
                    client,
                    RSS_URL,
                    headers=self.DEFAULT_HEADERS,
                    timeout=25.0,
                )
            )

        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("Failed to parse Impactpool RSS XML: %s", exc)
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> in Impactpool RSS feed")
            return []

        items = channel.findall("item")
        all_jobs = self._process_raw_jobs(items)

        if query:
            q_lower = query.lower()
            all_jobs = [
                j for j in all_jobs
                if q_lower in f"{j['title']} {j['description']}".lower()
            ]

        return self._finalize_fetch(all_jobs)

    def normalize_job(self, raw: ET.Element) -> dict:
        """Transform an RSS <item> into the unified job schema."""
        item = raw

        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        description_html = item.findtext("description") or ""
        description = strip_html_tags(description_html)

        # Empresa: Impactpool suele poner "Role — Organisation" o "Role | Organisation"
        company = ""
        for sep in [" — ", " | ", " - "]:
            if sep in title:
                parts = title.split(sep, 1)
                title, company = parts[0].strip(), parts[1].strip()
                break

        # Ubicación desde el título o descripción
        location_str = _extract_location(title, description)

        tags = extract_job_skills(title, description)

        return {
            "hash": self.compute_hash(title, company, url or guid),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location_str,
            "canton": extract_canton(location_str),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url or guid,
            "remote": "remote" in location_str.lower() or "home-based" in description.lower(),
            "tags": tags[: self.MAX_TAGS],
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": "en",  # Impactpool opera mayoritariamente en inglés
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }


def _extract_location(title: str, description: str) -> str:
    """Intenta extraer la ubicación del título o descripción."""
    combined = (title + " " + description).lower()
    # Ciudades frecuentes en puestos internacionales
    _known_locations = {
        "geneva": "Geneva, Switzerland",
        "new york": "New York, USA",
        "nairobi": "Nairobi, Kenya",
        "washington": "Washington DC, USA",
        "rome": "Rome, Italy",
        "paris": "Paris, France",
        "brussels": "Brussels, Belgium",
        "vienna": "Vienna, Austria",
        "london": "London, UK",
        "zurich": "Zurich, Switzerland",
        "bern": "Bern, Switzerland",
    }
    for key, label in _known_locations.items():
        if key in combined:
            return label
    if "home-based" in combined or "remote" in combined:
        return "Remote / Worldwide"
    return "International"
