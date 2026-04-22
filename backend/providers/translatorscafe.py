"""Provider for TranslatorsCafe job board (RSS feed).

TranslatorsCafe is the world's largest community marketplace for
language professionals — 460K+ registered translators and agencies.
Covers roles directly aligned with Alicia's profile: translator,
proofreader, editor, LQA reviewer, post-editor, interpreter.

RSS: https://www.translatorscafe.com/cafe/rss.aspx?type=joboffers
"""

import logging
import xml.etree.ElementTree as ET

import httpx

from services.job_service import BaseJobProvider
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

RSS_URL = "https://www.translatorscafe.com/cafe/rss.aspx?type=joboffers"


class TranslatorsCafeProvider(BaseJobProvider):
    """Fetch translation and localization jobs from TranslatorsCafe RSS."""

    SOURCE_NAME = "translatorscafe"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch jobs from TranslatorsCafe RSS feed."""
        async with httpx.AsyncClient() as client:
            xml_text = await self._circuit.call(
                lambda: fetch_rss(
                    client,
                    RSS_URL,
                    headers={**self.DEFAULT_HEADERS, "Accept": "application/rss+xml, application/xml"},
                    timeout=20.0,
                )
            )

        if not xml_text:
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("Failed to parse TranslatorsCafe RSS XML: %s", exc)
            return []

        # Soporta tanto RSS 2.0 como Atom
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else root.findall(".//{http://www.w3.org/2005/Atom}entry")
        all_jobs = self._process_raw_jobs(items)

        if query:
            q_lower = query.lower()
            all_jobs = [
                j for j in all_jobs
                if q_lower in f"{j['title']} {j['description']}".lower()
            ]

        return self._finalize_fetch(all_jobs)

    def normalize_job(self, raw: ET.Element) -> dict:
        """Transform an RSS <item> into the unified job schema."""
        item = raw

        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        description_html = item.findtext("description") or ""
        description = strip_html_tags(description_html)
        category = (item.findtext("category") or "").strip()

        # TranslatorsCafe suele identificar empresa en la descripción
        company = _extract_company(title, description)

        # Detectar par de idiomas para el campo language
        lang_code = _detect_primary_language(title + " " + description)

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
            "language": lang_code,
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }


def _extract_company(title: str, description: str) -> str:
    """Extrae empresa del título si el formato lo permite."""
    for sep in [" — ", " | ", " - "]:
        if sep in title:
            parts = title.split(sep, 1)
            if len(parts[0]) < 60:
                return parts[1].strip()
    return ""


def _detect_primary_language(text: str) -> str | None:
    """Detecta el idioma principal requerido en la oferta."""
    t = text.lower()
    if "english" in t or "en-" in t or "into english" in t or "native english" in t:
        return "en"
    if "spanish" in t or "es-" in t or "into spanish" in t:
        return "es"
    if "french" in t or "fr-" in t or "into french" in t:
        return "fr"
    if "japanese" in t or "ja-" in t or "into japanese" in t:
        return "ja"
    if "german" in t or "de-" in t or "into german" in t:
        return "de"
    return None
