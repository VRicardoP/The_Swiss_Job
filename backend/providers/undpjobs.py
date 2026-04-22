"""Provider for UNDP Jobs (United Nations Development Programme).

UNDP Jobs (jobs.undp.org) publishes an RSS 1.0/RDF feed updated hourly.
Covers admin, HR, programme, communications and operations roles globally —
with many remote/home-based positions, highly relevant for Alicia's profile.

RSS: https://jobs.undp.org/cj_rss_feed.cfm
"""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

RSS_URL = "https://jobs.undp.org/cj_rss_feed.cfm"

# Namespace RDF estándar RSS 1.0
_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_RSS_NS = "http://purl.org/rss/1.0/"
_DC_NS = "http://purl.org/dc/elements/1.1/"


class UNDPJobsProvider(BaseJobProvider):
    """Fetch UNDP job vacancies from their RSS 1.0 feed."""

    SOURCE_NAME = "undpjobs"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from UNDP RSS feed."""
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
            logger.error("Failed to parse UNDP RSS XML: %s", exc)
            return []

        # RSS 1.0 (RDF): items son hijos directos de <rdf:RDF>
        # o dentro de <channel>. Soporte para ambos formatos.
        items = _extract_items(root)
        if not items:
            logger.warning("No items found in UNDP RSS feed")
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
        """Transform an RSS 1.0 <item> into the unified job schema."""
        item = raw

        # Soporte para RSS 2.0 y RSS 1.0 (con namespace)
        title = (
            item.findtext("title")
            or item.findtext(f"{{{_RSS_NS}}}title")
            or ""
        ).strip()

        url = (
            item.findtext("link")
            or item.findtext(f"{{{_RSS_NS}}}link")
            or item.get(f"{{{_RDF_NS}}}about")
            or ""
        ).strip()

        guid = (item.findtext("guid") or "").strip()

        description_html = (
            item.findtext("description")
            or item.findtext(f"{{{_RSS_NS}}}description")
            or ""
        )
        description = strip_html_tags(description_html)

        # dc:subject puede contener categoría del rol
        category = (
            item.findtext(f"{{{_DC_NS}}}subject")
            or item.findtext("category")
            or ""
        ).strip()

        # Empresa es siempre UNDP en este feed
        company = "UNDP"

        # Ubicación: extraer del título o descripción
        location_str = _extract_location(title, description)

        tags = extract_job_skills(title, description)
        if category and category.lower() not in [t.lower() for t in tags]:
            tags = [category] + tags

        is_remote = "home-based" in location_str.lower() or "remote" in location_str.lower()

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
            "contract_type": None,
            "employment_type": None,
        }


def _extract_items(root: ET.Element) -> list[ET.Element]:
    """Extrae items de RSS 2.0 o RSS 1.0 (RDF)."""
    # Intentar RSS 2.0: <channel><item>
    channel = root.find("channel")
    if channel is not None:
        items = channel.findall("item")
        if items:
            return items

    # RSS 1.0: <rdf:RDF><item> con namespace
    items = root.findall(f"{{{_RSS_NS}}}item")
    if items:
        return items

    # Fallback: buscar <item> en cualquier parte del árbol
    return root.findall(".//item")


def _extract_location(title: str, description: str) -> str:
    """Extrae ubicación del título o descripción del job."""
    combined = f"{title} {description}".lower()

    if "home-based" in combined or "home based" in combined:
        return "Remote / Home-based"
    if "remote" in combined[:400]:
        return "Remote / Worldwide"

    # Ciudades frecuentes en UNDP
    _CITIES = [
        "New York", "Geneva", "Nairobi", "Bangkok", "Brussels",
        "Washington", "Istanbul", "Cairo", "Dakar", "Addis Ababa",
        "Panama City", "Amman", "Beirut", "Kabul", "Islamabad",
    ]
    for city in _CITIES:
        if city.lower() in combined[:500]:
            return city

    return "International"
