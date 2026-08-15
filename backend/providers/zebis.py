"""Provider for zebis.ch — education and teaching jobs in German-speaking Switzerland (RSS)."""

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlsplit

import httpx

from services.job_service import BaseJobProvider
from utils.dates import parse_published_at
from utils.http import fetch_rss
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

BASE_URL = "https://www.zebis.ch"
FEED_URL = f"{BASE_URL}/stellen/stelleninserate/rss"

# Pensum en el título, con o sin paréntesis. Formas REALES del feed
# (sonda 2026-08-14, 28 items): "(70-80%)", "(13 Lektionen / 46%)",
# "80 – 100 %", "Pensum bis ca. 50 %", ", 80 %", "40 - 100%", "(Total 130 %)".
# En esos 28 items un porcentaje en el título es SIEMPRE el pensum (convención
# suiza); el lookbehind evita morder dentro de años ("Schuljahr 2026/27").
_WORKLOAD_RE = re.compile(r"(?<!\d)(\d{1,3}(?:\s*[-–]\s*\d{1,3})?\s*%)")

# Path REAL de las ofertas del portal: /stellen/<slug>, sin segmentos extra
# ni caracteres de control (< 0x20 y DEL 0x7f: no deben acabar en una URL
# persistida). Residual asumido (V2-4, VD.9): un "%00" LITERAL (tres chars
# imprimibles de percent-encoding) sí pasa — calidad de dato sobre host
# propio (BASE_URL nuestro), sin riesgo. \t\n\r nunca llegan aquí: urlsplit
# los elimina antes de parsear (slug fusionado).
_STELLEN_PATH_RE = re.compile(r"^/stellen/[^/?#\x00-\x1f\x7f]+$")


def _canonical_job_url(raw: str) -> str | None:
    """Reconstruye la URL canónica de la oferta a partir de un link/guid del
    feed, o None si no es utilizable.

    El portal tiene la base del feed MAL configurada (verificado 2026-08-14):
    emite `<link>` y `<guid>` como `https://0.0.0.0:3000/stellen/<slug>`
    (`xml:base="https://0.0.0.0:3000/"`) y ningún campo trae el host bueno.
    Por eso NUNCA se confía en el host del feed: se toma solo el path, se
    valida y se reconstituye sobre BASE_URL (la URL resultante responde 200
    con la oferta correcta). Mismas defensas que `_resolve_job_url()` de
    scrapers/stelle_admin.py — ver allí el detalle de cada rechazo.
    """
    # Diferencial urllib/WHATWG: urlsplit no corta la autoridad en "\", el
    # navegador sí — se rechaza antes de parsear.
    if not raw or "\\" in raw:
        return None
    try:
        parts = urlsplit(raw)
    except ValueError:
        # p. ej. "https://[evil/..." (IPv6 inválido): urlsplit LANZA en vez
        # de parsear. Sin capturarlo la excepción escapaba y el fallback al
        # guid del caller nunca llegaba a ejecutarse (oferta legítima perdida).
        return None
    # Solo http(s) y sin userinfo (spoofing visual del enlace).
    if parts.scheme not in ("http", "https") or parts.username is not None:
        return None
    # Ni "/", ni "//evil.com/x" (protocolo-relativo: el "host" cae en netloc
    # y el path no casa), ni paths ajenos al listado de ofertas.
    if not _STELLEN_PATH_RE.match(parts.path):
        return None
    # El host es SIEMPRE una constante nuestra, jamás el del feed.
    url = f"{BASE_URL}{parts.path}"
    if urlsplit(url).hostname != "www.zebis.ch":  # defensa en profundidad
        return None
    return url


def _extract_employer(description_html: str) -> str:
    """Try to extract employer name from the first <strong> or <p><strong> in description.

    Evaluado sobre el feed real (sonda 2026-08-14, 28 items): la description
    llega ahora en TEXTO PLANO truncado (~600 chars, sin <strong>), y el
    empleador aparece en posiciones libres del texto ("An der Schule X...",
    "Die Casa Babetta ist...", "Willkommen in Glarus Nord..." — que ni
    siquiera es el empleador) o directamente no aparece en el prefijo
    visible. No hay patrón fiable → NO se añade heurística especulativa:
    company queda "Unknown". Se conserva este parser por si el portal
    restaura el HTML con <strong> que emitía antes.
    """
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
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        # Base rota del portal (0.0.0.0:3000, 2026-08-14): la URL se
        # canonicaliza SIEMPRE sobre BASE_URL. Sin URL utilizable la oferta
        # no se emite — con url vacía, _process_raw_jobs la descarta y la
        # loguea (misma regla que stelle_admin: una oferta sin URL propia
        # no es utilizable).
        url = _canonical_job_url(link) or _canonical_job_url(guid) or ""
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
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location,
            "canton": canton,
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
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
            # Fecha del PORTAL (pubDate RFC822 del feed RSS).
            "published_at": parse_published_at(item.findtext("pubDate")),
        }
