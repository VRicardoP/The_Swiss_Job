"""Provider for We Work Remotely (RSS feed)."""

import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from services.job_service import BaseJobProvider
from utils.dates import parse_published_at
from utils import fetch_diagnostics as diag
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

# Namespace for media:content elements
MRSS_NS = "http://search.yahoo.com/mrss/"


class WeWorkRemotelyProvider(BaseJobProvider):
    """Fetch remote jobs from We Work Remotely RSS feed."""

    SOURCE_NAME = "weworkremotely"
    API_URL = "https://weworkremotely.com/remote-jobs.rss"

    def _record_structure_failure(self, detail: str) -> None:
        """Registra un fallo de estructura como error de fetch VISIBLE.

        G3/P2-6: un HTTP 200 cuyo cuerpo no podemos leer NO es "no hay
        ofertas". Sin este registro el veredicto del run salía `empty` (sequía
        legítima) en vez de `error` y el panel de salud daba por sana una
        fuente rota. Mismo patrón que zebis (clase V.0/VD.7).
        """
        logger.error("weworkremotely: %s", detail)
        diag.record(diag.KIND_NETWORK, self.API_URL, detail=detail)

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch remote jobs from We Work Remotely RSS feed."""
        async with httpx.AsyncClient() as client:
            xml_text = await self._circuit.call(
                lambda: fetch_rss(client, self.API_URL, headers=self.DEFAULT_HEADERS)
            )

        # G3/P2-6: SOLO el None de fetch_rss corta aquí (fetch fallido cuyo
        # issue ya registró utils.http). Un "" —200 con cuerpo vacío— fluye a
        # ET.fromstring y sale como fallo de estructura, no como feed vacío.
        if xml_text is None:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            self._record_structure_failure(f"RSS XML ilegible: {e}")
            return []

        channel = root.find("channel")
        if channel is None:
            self._record_structure_failure("RSS sin <channel>: estructura desconocida")
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

    def normalize_job(self, raw: Any) -> dict:
        """Transform an RSS <item> XML element into the unified job schema."""
        item: ET.Element = raw

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
            # Fecha del PORTAL (pubDate RFC822 del feed RSS).
            "published_at": parse_published_at(item.findtext("pubDate")),
        }
