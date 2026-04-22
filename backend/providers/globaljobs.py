"""Provider for GlobalJobs international careers (RSS feed).

GlobalJobs.org is a curated board for international, NGO, think-tank
and non-profit careers. Covers roles aligned with Alicia's profile:
programme assistant, documentation, language assistant, admin support,
HR officer, conference services — especially for international orgs.

RSS: https://www.globaljobs.org/jobs/feed.rss
"""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

RSS_URL = "https://www.globaljobs.org/jobs/feed.rss"


class GlobalJobsProvider(BaseJobProvider):
    """Fetch international and NGO jobs from GlobalJobs RSS feed."""

    SOURCE_NAME = "globaljobs"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from GlobalJobs RSS."""
        async with httpx.AsyncClient() as client:
            xml_text = await self._circuit.call(
                lambda: fetch_rss(
                    client,
                    RSS_URL,
                    headers=self.DEFAULT_HEADERS,
                    timeout=20.0,
                )
            )

        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("Failed to parse GlobalJobs RSS XML: %s", exc)
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> in GlobalJobs RSS feed")
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
        category = (item.findtext("category") or "").strip()

        # Formato habitual GlobalJobs: "Role at Organization" o "Role — Organization"
        company = ""
        if " at " in title:
            parts = title.rsplit(" at ", 1)
            title, company = parts[0].strip(), parts[1].strip()
        elif " — " in title:
            parts = title.split(" — ", 1)
            title, company = parts[0].strip(), parts[1].strip()
        elif " | " in title:
            parts = title.split(" | ", 1)
            title, company = parts[0].strip(), parts[1].strip()

        # Extrae ubicación del texto de descripción
        location_str = _extract_location(description)

        tags = extract_job_skills(title, description)
        if category and category.lower() not in [t.lower() for t in tags]:
            tags = [category] + tags

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
            "remote": "remote" in location_str.lower() or "home-based" in location_str.lower(),
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


# Ciudades clave de organismos internacionales para extracción de ubicación
_INTL_CITIES = [
    "Geneva", "New York", "Vienna", "Brussels", "Paris", "Nairobi",
    "Washington", "London", "Rome", "The Hague", "Bonn", "Bangkok",
    "Bangkok", "Addis Ababa", "Cairo", "Dakar", "Beirut", "Amman",
]


def _extract_location(description: str) -> str:
    """Intenta extraer ubicación de la descripción del job."""
    text_lower = description.lower()

    if "home-based" in text_lower or "home based" in text_lower:
        return "Remote / Home-based"
    if "remote" in text_lower[:300]:
        return "Remote / Worldwide"

    for city in _INTL_CITIES:
        if city.lower() in text_lower[:500]:
            return city

    return "International"
