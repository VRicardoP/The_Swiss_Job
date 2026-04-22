"""Provider for ILO Jobs (International Labour Organization).

ILO Jobs (jobs.ilo.org) uses WordPress Job Manager which exposes
a standard RSS feed. This provider handles the feed gracefully —
if the endpoint doesn't respond or returns no jobs, it fails silently.

RSS (WordPress Job Manager standard): https://jobs.ilo.org/?feed=job_feed
"""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

RSS_URL = "https://jobs.ilo.org/?feed=job_feed"

# Namespace de WordPress Job Manager
_WP_JOB_NS = "http://schemas.google.com/spreadsheets/2006"


class ILOJobsProvider(BaseJobProvider):
    """Fetch ILO job vacancies via WordPress Job Manager RSS feed.

    El feed es experimental — WordPress Job Manager expone un endpoint
    estándar (?feed=job_feed) que puede o no estar activo en este portal.
    Si falla, el CircuitBreaker lo desactiva temporalmente.
    """

    SOURCE_NAME = "ilojobs"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch ILO jobs from WordPress RSS feed."""
        async with httpx.AsyncClient() as client:
            xml_text = await self._circuit.call(
                lambda: fetch_rss(
                    client,
                    RSS_URL,
                    headers={
                        **self.DEFAULT_HEADERS,
                        "Accept": "application/rss+xml, application/xml, text/xml",
                    },
                    timeout=25.0,
                )
            )

        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("Failed to parse ILO Jobs RSS XML: %s", exc)
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> in ILO Jobs RSS feed")
            return []

        items = channel.findall("item")
        if not items:
            logger.info("ILO Jobs RSS feed returned 0 items")
            return []

        all_jobs = self._process_raw_jobs(items)

        if query:
            q_lower = query.lower()
            all_jobs = [
                j for j in all_jobs
                if q_lower in f"{j['title']} {j['description']}".lower()
            ]

        return self._finalize_fetch(all_jobs)

    def normalize_job(self, raw: ET.Element) -> dict:
        """Transform a WordPress Job Manager <item> into the unified schema."""
        item = raw

        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        description_html = item.findtext("description") or ""
        description = strip_html_tags(description_html)
        category = (item.findtext("category") or "").strip()

        # WordPress Job Manager puede incluir metadatos de ubicación y tipo
        job_location = _find_wp_meta(item, "job_location") or "Geneva, Switzerland"
        job_type = _find_wp_meta(item, "job_type") or ""

        # La empresa es la OIT (ILO)
        company = "ILO"

        tags = extract_job_skills(title, description)
        if category and category.lower() not in [t.lower() for t in tags]:
            tags = [category] + tags

        contract_type = _map_job_type(job_type)
        is_remote = "remote" in job_location.lower() or "home-based" in job_location.lower()

        return {
            "hash": self.compute_hash(title, company, url or guid),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": job_location,
            "canton": extract_canton(job_location),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url or guid,
            "remote": is_remote,
            "tags": tags[: self.MAX_TAGS],
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": "en",
            "seniority": None,
            "contract_type": contract_type,
            "employment_type": job_type or None,
        }


def _find_wp_meta(item: ET.Element, field: str) -> str:
    """Busca un campo de metadata de WordPress Job Manager en el item RSS."""
    # Puede aparecer como elemento directo o con namespace
    for ns_prefix in ["", "{http://www.w3.org/2005/Atom}"]:
        val = item.findtext(f"{ns_prefix}{field}")
        if val:
            return val.strip()
    return ""


def _map_job_type(job_type: str) -> str | None:
    """Mapea tipo de trabajo WP Job Manager a enum unificado."""
    if not job_type:
        return None
    t = job_type.lower()
    if "short-term" in t or "temporary" in t or "fixed" in t:
        return "temporary"
    if "consultancy" in t or "consultant" in t or "contract" in t:
        return "contract"
    if "internship" in t or "intern" in t:
        return "internship"
    if "part" in t:
        return "part_time"
    if "full" in t or "regular" in t or "permanent" in t:
        return "full_time"
    return None
