"""Provider for Jobspresso remote jobs (WordPress RSS feed).

Jobspresso is a curated remote job board covering development, design,
marketing, writing, and customer support roles.

RSS: https://jobspresso.co/feed/?post_type=job_listing
"""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

RSS_URL = "https://jobspresso.co/feed/?post_type=job_listing"

_TECH_EXCLUDE_TITLES = {
    "software engineer", "backend engineer", "frontend engineer",
    "full stack", "fullstack", "devops", "sre", "ml engineer",
    "data engineer", "cloud engineer", "mobile developer",
    "ios developer", "android developer", "blockchain",
    "cybersecurity", "security engineer", "embedded",
}


class JobspressoProvider(BaseJobProvider):
    """Fetch remote jobs from Jobspresso WordPress RSS feed."""

    SOURCE_NAME = "jobspresso"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from Jobspresso RSS."""
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
            logger.error("Failed to parse Jobspresso RSS XML: %s", exc)
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> in Jobspresso RSS feed")
            return []

        items = channel.findall("item")
        all_jobs = self._process_raw_jobs(items)
        filtered = [
            j for j in all_jobs
            if not any(kw in j.get("title", "").lower() for kw in _TECH_EXCLUDE_TITLES)
        ]

        if query:
            q_lower = query.lower()
            filtered = [
                j for j in filtered
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

        # WordPress job listings suelen tener creator o author como empresa
        # Intentar extraer "at Company" del título
        company = ""
        if " at " in title:
            parts = title.rsplit(" at ", 1)
            title, company = parts[0].strip(), parts[1].strip()

        # La ubicación en WordPress job_listing puede estar en un custom field
        location_str = _extract_location(item, description)
        category = (item.findtext("category") or "").strip()
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
            "remote": "remote" in location_str.lower(),
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


def _extract_location(item: ET.Element, description: str) -> str:
    """Intenta extraer ubicación del item RSS de WordPress."""
    # WordPress job_listing puede usar namespaces para la ubicación
    for ns in ["job_listing", ""]:
        prefix = f"{{{ns}}}" if ns else ""
        loc = item.findtext(f"{prefix}job_location")
        if loc:
            return loc.strip()
    if "remote" in description.lower()[:200]:
        return "Remote / Worldwide"
    return "Remote / Worldwide"
