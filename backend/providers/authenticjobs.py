"""Provider for Authentic Jobs remote jobs (RSS feed).

Authentic Jobs is a curated job board for designers, developers, and
content professionals. Their customizable RSS supports remote-only filtering.
Relevant for Alicia: content editor, copywriter, UX writer, project manager.

RSS (remote only): https://authenticjobs.com/rss/custom.php?remote=1
"""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

# Feed filtrado a roles remote únicamente
RSS_URL = "https://authenticjobs.com/rss/custom.php?remote=1"

_TECH_EXCLUDE_TITLES = {
    "software engineer", "backend engineer", "frontend engineer",
    "full stack", "fullstack", "devops", "sre", "ml engineer",
    "data engineer", "cloud engineer", "mobile developer",
    "ios developer", "android developer", "blockchain",
    "cybersecurity", "security engineer", "embedded", "firmware",
}


class AuthenticJobsProvider(BaseJobProvider):
    """Fetch remote content and creative jobs from Authentic Jobs RSS."""

    SOURCE_NAME = "authenticjobs"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from Authentic Jobs RSS."""
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
            logger.error("Failed to parse Authentic Jobs RSS XML: %s", exc)
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> in Authentic Jobs RSS feed")
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

        # Formato típico Authentic Jobs: "Role at Company"
        company = ""
        if " at " in title:
            parts = title.rsplit(" at ", 1)
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
