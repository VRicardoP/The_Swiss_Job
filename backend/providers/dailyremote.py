"""Provider for DailyRemote jobs (RSS feed).

DailyRemote aggregates remote jobs across many categories including
writing, customer support, HR, design, and non-tech roles — highly
relevant for Alicia's profile.

RSS feed: https://dailyremote.com/rss
"""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

RSS_URL = "https://dailyremote.com/rss"

# Categorías relevantes para el perfil (se filtra el resto)
_RELEVANT_CATEGORIES = {
    "writing",
    "content",
    "customer support",
    "human resources",
    "hr",
    "education",
    "data",
    "admin",
    "operations",
    "marketing",
    "translation",
    "localization",
    "qa",
    "proofreading",
    "social media",
    "project management",
    "non-tech",
    "all other remote",
}


class DailyRemoteProvider(BaseJobProvider):
    """Fetch remote jobs from DailyRemote RSS feed."""

    SOURCE_NAME = "dailyremote"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from DailyRemote RSS."""
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
            logger.error("Failed to parse DailyRemote RSS XML: %s", exc)
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> in DailyRemote RSS feed")
            return []

        items = channel.findall("item")
        all_jobs = self._process_raw_jobs(items)

        # Filtra por categoría relevante y opcionalmente por query
        filtered = self._filter_relevant(all_jobs)
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
        category = (item.findtext("category") or "").strip().lower()

        # Intenta extraer empresa del título si formato es "Role at Company"
        company = ""
        if " at " in title:
            parts = title.rsplit(" at ", 1)
            title, company = parts[0].strip(), parts[1].strip()

        tags = extract_job_skills(title, description)
        if category and category not in [t.lower() for t in tags]:
            tags = [category.title()] + tags

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
            "language": "en",  # DailyRemote es mayoritariamente EN
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }

    @staticmethod
    def _filter_relevant(jobs: list[dict]) -> list[dict]:
        """Descarta empleos tech-only para reducir ruido irrelevante."""
        _EXCLUDE_KEYWORDS = {
            "software engineer", "backend engineer", "frontend engineer",
            "full stack", "fullstack", "devops", "sre", "ml engineer",
            "data engineer", "cloud engineer", "mobile developer",
            "ios developer", "android developer", "blockchain",
            "cybersecurity", "security engineer", "qa engineer",
            "embedded", "firmware", "hardware",
        }
        result = []
        for job in jobs:
            title_lower = job.get("title", "").lower()
            if any(kw in title_lower for kw in _EXCLUDE_KEYWORDS):
                continue
            result.append(job)
        return result
