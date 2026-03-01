"""Provider for zebis.ch — education and teaching jobs in German-speaking Switzerland (RSS)."""

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

DC_NS = "http://purl.org/dc/elements/1.1/"
BASE_URL = "https://www.zebis.ch"
FEED_URL = f"{BASE_URL}/stellen/stelleninserate/rss"

# Pattern to extract percentage workload from title, e.g. "Lehrperson (80 %)"
_WORKLOAD_RE = re.compile(r"\((\d[\d\s\-–]+%)\)\s*$")


def _extract_employer(description_html: str) -> str:
    """Try to extract employer name from the first <strong> or <p><strong> in description."""
    match = re.search(r"<(?:p|div)>\s*<strong>([^<]+)</strong>", description_html)
    if match:
        name = match.group(1).strip()
        # Avoid false positives like dates or generic phrases
        if len(name) > 3 and not name[0].isdigit():
            return name
    return "Unknown"


class ZebisProvider(BaseJobProvider):
    """Fetch education jobs from zebis.ch RSS feed."""

    SOURCE_NAME = "zebis"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch teaching jobs from zebis.ch RSS feed."""
        async with httpx.AsyncClient() as client:
            xml_text = await self._circuit.call(
                lambda: fetch_rss(client, FEED_URL, headers=self.DEFAULT_HEADERS)
            )

        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error("Failed to parse zebis RSS XML: %s", e)
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> element in zebis RSS feed")
            return []

        items = channel.findall("item")
        all_jobs = self._process_raw_jobs(items)

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

    def normalize_job(self, raw: Any) -> dict:
        """Transform an RSS <item> XML element into the unified job schema."""
        item: ET.Element = raw

        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        description_html = item.findtext("description") or ""
        description = strip_html_tags(description_html)

        # Extract employer from description HTML (first bold text)
        company = _extract_employer(description_html)

        # Extract workload percentage from title if present
        workload_match = _WORKLOAD_RE.search(title)
        employment_type = workload_match.group(1) if workload_match else None

        # Location hints from description (municipality/canton mentions)
        location = "Switzerland"
        canton = extract_canton(description)

        tags = extract_job_skills(title, description)

        return {
            "hash": self.compute_hash(title, company, url or guid),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location,
            "canton": canton,
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url or guid,
            "remote": False,
            "tags": tags[: self.MAX_TAGS],
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": employment_type,
        }
