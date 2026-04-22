"""Provider for ProZ.com translation and localization jobs (RSS feed).

ProZ.com is the world's largest community of professional translators.
Covers roles highly relevant to Alicia: localization specialist, LQA,
content editor, translator EN/ES/JA, post-editor, proofreader.

RSS feed: https://www.proz.com/rss/jobs/
"""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

RSS_URL = "https://www.proz.com/rss/jobs/"


class ProzProvider(BaseJobProvider):
    """Fetch localization and translation jobs from ProZ.com RSS feed."""

    SOURCE_NAME = "proz"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from ProZ.com RSS feed and filter by query."""
        async with httpx.AsyncClient() as client:
            xml_text = await self._circuit.call(
                lambda: fetch_rss(
                    client,
                    RSS_URL,
                    headers={**self.DEFAULT_HEADERS, "Accept": "application/rss+xml"},
                    timeout=20.0,
                )
            )

        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("Failed to parse ProZ RSS XML: %s", exc)
            return []

        channel = root.find("channel")
        if channel is None:
            logger.warning("No <channel> in ProZ RSS feed")
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
        """Transform an RSS <item> element into the unified job schema."""
        item = raw

        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        description_html = item.findtext("description") or ""
        description = strip_html_tags(description_html)

        # ProZ items typically have: "Company Name — Job Title" or just title
        company = ""
        if " — " in title:
            parts = title.split(" — ", 1)
            company, title = parts[0].strip(), parts[1].strip()
        elif " - " in title:
            parts = title.split(" - ", 1)
            company, title = parts[0].strip(), parts[1].strip()

        # Categoría desde la descripción
        category = (item.findtext("category") or "").strip()

        tags = extract_job_skills(title, description)
        if category and category not in tags:
            tags = [category] + tags

        # Detectar idiomas en título/descripción para el campo language
        lang_code = _detect_language_pair(title + " " + description)

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
            "language": lang_code,
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }


def _detect_language_pair(text: str) -> str | None:
    """Heurística: si el texto menciona English pair, retorna 'en'."""
    t = text.lower()
    if "english" in t or "en-" in t or " en>" in t:
        return "en"
    if "spanish" in t or "es-" in t:
        return "es"
    if "french" in t or "fr-" in t:
        return "fr"
    if "japanese" in t or "ja-" in t:
        return "ja"
    return None
