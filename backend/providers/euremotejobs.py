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
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

RSS_URL = "https://euremotejobs.com/feed/"

# Palabras en título que indican roles técnicos a descartar
_TECH_EXCLUDE = {
    "software engineer", "backend engineer", "frontend engineer",
    "full stack", "fullstack", "devops", "sre", "site reliability",
    "ml engineer", "data engineer", "cloud engineer", "platform engineer",
    "mobile developer", "ios developer", "android developer",
    "blockchain", "cybersecurity", "security engineer", "embedded",
    "firmware", "hardware engineer", "network engineer",
}


class EURemoteJobsProvider(BaseJobProvider):
    """Fetch European remote jobs from EU Remote Jobs RSS feed."""

    SOURCE_NAME = "euremotejobs"

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

        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("Failed to parse EU Remote Jobs RSS XML: %s", exc)
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> in EU Remote Jobs RSS feed")
            return []

        items = channel.findall("item")
        all_jobs = self._process_raw_jobs(items)
        filtered = self._exclude_tech_roles(all_jobs)

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
