"""Provider for We Work Remotely (RSS feed)."""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

# Namespace for media:content elements
MRSS_NS = "http://search.yahoo.com/mrss/"


class WeWorkRemotelyProvider(BaseJobProvider):
    """Fetch remote jobs from We Work Remotely RSS feed."""

    SOURCE_NAME = "weworkremotely"
    API_URL = "https://weworkremotely.com/remote-jobs.rss"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from We Work Remotely RSS feed."""
        async with httpx.AsyncClient() as client:
            xml_text = await self._circuit.call(
                lambda: fetch_rss(client, self.API_URL, headers=self.DEFAULT_HEADERS)
            )

        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error("Failed to parse WWR RSS XML: %s", e)
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> element found in WWR RSS feed")
            return []

        items = channel.findall("item")
        all_jobs = self._process_raw_jobs(items)

        # Filter by query if provided
        if query:
            q_lower = query.lower()
            results = [
                job
                for job in all_jobs
                if q_lower
                in f"{job['title']} {job['company']} {job['description']}".lower()
            ]
        else:
            results = all_jobs

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        """Transform an RSS <item> XML element into the unified job schema.

        Note: ``raw`` here is actually an ``xml.etree.ElementTree.Element``.
        """
        item: ET.Element = raw  # type: ignore[assignment]

        # Title format: "Company Name: Job Title"
        full_title = (item.findtext("title") or "").strip()
        if ": " in full_title:
            company, title = full_title.split(": ", 1)
        else:
            company = ""
            title = full_title

        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        description_html = item.findtext("description") or ""
        description = strip_html_tags(description_html)
        region = (item.findtext("region") or "").strip()
        job_type = (item.findtext("type") or "").strip()

        # Logo from media:content
        media_content = item.find(f"{{{MRSS_NS}}}content")
        logo = None
        if media_content is not None:
            logo = media_content.get("url")

        location_str = region if region else "Remote / Worldwide"

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
            "remote": True,
            "tags": tags,
            "logo": logo,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": job_type if job_type else None,
        }
