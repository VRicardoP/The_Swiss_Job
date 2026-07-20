"""Provider para la API pública NAV Arbeidsplassen (bolsa de empleo de Noruega).

La API (`/stillinger/api/search`) es un GET con query params que devuelve un dump
crudo de Elasticsearch: las ofertas viven en `response["hits"]["hits"][]._source`
y el total en `response["hits"]["total"]["value"]`. El filtro remoto se hace con
el param `remote=<valor noruego exacto>` (facetas descubiertas en vivo, ver
REMOTE_FACETS). Solo se cosechan ofertas remotas.
"""

import asyncio
import logging

import httpx

from services.circuit_breaker import CircuitBreakerOpen
from services.job_service import BaseJobProvider
from utils.http import fetch_with_retry
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

# Facetas remote EXACTAS de la API (confirmadas en vivo desde
# aggregations.remote): solo estas dos implican posibilidad de trabajo remoto.
# "Ikke oppgitt" (sin especificar) e "Ingen mulighet for hjemmekontor" (sin
# opción de teletrabajo) NO son remotas. Se cosechan por separado y se
# deduplica por uuid porque una oferta puede clasificarse en una sola faceta.
REMOTE_FACETS = ("Kun hjemmekontor", "Delvis hjemmekontor")

# Tamaño de página de Elasticsearch (from/size). 100 = eficiente sin abusar.
PAGE_SIZE = 100

# Prefijo de la URL pública de detalle: se completa con el uuid (_id).
DETAIL_URL_PREFIX = "https://arbeidsplassen.nav.no/stillinger/stilling/"

# Pausa cortés entre páginas de una misma faceta.
PAGE_DELAY_SECONDS = 0.5

# Mapeo de workLanguage noruego → código de idioma corto para el campo
# `language`. Nynorsk/Bokmål/Samisk/Skandinavisk se agrupan como "no".
_LANGUAGE_MAP = {
    "Norsk": "no",
    "Skandinavisk": "no",
    "Samisk": "no",
    "Engelsk": "en",
}


class NavArbeidsplassenProvider(BaseJobProvider):
    """Cosecha ofertas remotas de la API pública NAV Arbeidsplassen (Noruega)."""

    SOURCE_NAME = "nav_arbeidsplassen"
    API_URL = "https://arbeidsplassen.nav.no/stillinger/api/search"
    # Techo de páginas POR FACETA (size=100 → hasta 300 ofertas por faceta).
    MAX_PAGES = 3

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Cosecha ambas facetas remote, deduplica por uuid y normaliza."""
        seen_uuids: set[str] = set()
        results: list[dict] = []

        async with httpx.AsyncClient() as client:
            for facet in REMOTE_FACETS:
                raw_hits = await self._fetch_facet(client, facet)
                # Dedup por uuid (_id): la misma oferta no debe contarse dos veces.
                unique = [h for h in raw_hits if self._register_uuid(h, seen_uuids)]
                results.extend(self._process_raw_jobs(unique))

        return self._finalize_fetch(results)

    @staticmethod
    def _register_uuid(raw: dict, seen: set[str]) -> bool:
        """Registra el uuid del hit; devuelve True solo la primera vez que se ve."""
        uuid = (raw.get("_id") or "").strip()
        if not uuid or uuid in seen:
            return False
        seen.add(uuid)
        return True

    async def _fetch_facet(self, client: httpx.AsyncClient, facet: str) -> list[dict]:
        """Pagina una faceta remote hasta agotar resultados o alcanzar MAX_PAGES.

        Robustez: una página vacía (from >= total) es ÉXITO (HTTP 200) y termina
        la faceta sin error. Un fallo de red (fetch devuelve None) o el breaker
        abierto cortan ESTA faceta pero no abortan las demás.
        """
        hits: list[dict] = []
        budget = self._pages_budget()
        for page in range(budget):
            offset = page * PAGE_SIZE
            params = {"from": offset, "size": PAGE_SIZE, "remote": facet}
            try:
                data = await self._circuit.call(
                    lambda p=params: fetch_with_retry(client, self.API_URL, params=p)
                )
            except CircuitBreakerOpen as exc:
                logger.warning("NAV faceta '%s' omitida (breaker): %s", facet, exc)
                break

            if not data:
                break

            page_hits = data.get("hits", {}).get("hits", [])
            if not page_hits:
                # Fin de la faceta: página vacía es éxito, no fallo.
                break

            hits.extend(page_hits)

            total = data.get("hits", {}).get("total", {}).get("value", 0)
            if offset + PAGE_SIZE >= total:
                break

            if page < budget - 1:
                await asyncio.sleep(PAGE_DELAY_SECONDS)

        return hits

    def normalize_job(self, raw: dict) -> dict:
        """Transforma un hit de Elasticsearch de NAV al esquema unificado."""
        uuid = (raw.get("_id") or "").strip()
        source = raw.get("_source") or {}

        title = (source.get("title") or "").strip()
        company = self._extract_company(source)
        url = f"{DETAIL_URL_PREFIX}{uuid}" if uuid else ""

        description = self._extract_description(source)
        location_raw = self._extract_location(source)
        props = source.get("properties") or {}

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location_raw,
            "canton": extract_canton(location_raw),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            # Todas las ofertas cosechadas vienen de una faceta remote → True.
            "remote": True,
            "tags": self._build_tags(title, description, props),
            "logo": None,
            # NAV no expone salario en la búsqueda → None (nunca strings).
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": self._detect_language(props),
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }

    @staticmethod
    def _extract_company(source: dict) -> str:
        """Nombre de empresa: businessName (comercial) con fallback employer.name."""
        name = (source.get("businessName") or "").strip()
        if name:
            return name
        employer = source.get("employer") or {}
        return (employer.get("name") or "").strip()

    @staticmethod
    def _extract_description(source: dict) -> str:
        """Descripción: el listado NO trae el anuncio completo (adtext), así que se
        usa el resumen generado (shortSummary), presente en casi todas las ofertas.

        Evita una segunda llamada HTTP por oferta.
        """
        meta = source.get("generatedSearchMetadata") or {}
        return strip_html_tags(meta.get("shortSummary") or "")

    @staticmethod
    def _extract_location(source: dict) -> str:
        """Primera ubicación legible de locationList (municipio, condado, país).

        Deduplica partes repetidas (p.ej. municipio == condado == "OSLO").
        """
        for loc in source.get("locationList") or []:
            candidates = [
                loc.get("city") or loc.get("municipal"),
                loc.get("county"),
                loc.get("country"),
            ]
            seen: set[str] = set()
            parts: list[str] = []
            for candidate in candidates:
                value = (candidate or "").strip()
                if value and value.lower() not in seen:
                    seen.add(value.lower())
                    parts.append(value)
            if parts:
                return ", ".join(parts)
        return ""

    def _build_tags(self, title: str, description: str, props: dict) -> list[str]:
        """Combina las etiquetas de NAV (searchtagsai, ya strings limpias) con las
        skills extraídas del texto, deduplicando sin distinguir mayúsculas."""
        api_tags = props.get("searchtagsai") or []
        extracted = extract_job_skills(title, description)
        seen: set[str] = set()
        merged: list[str] = []
        for tag in list(api_tags) + extracted:
            tag_str = str(tag).strip()
            if tag_str and tag_str.lower() not in seen:
                seen.add(tag_str.lower())
                merged.append(tag_str)
        return merged[: self.MAX_TAGS]

    @staticmethod
    def _detect_language(props: dict) -> str:
        """Deriva el idioma del anuncio de workLanguage (campo estructural).

        Prioriza el primer idioma reconocido de la lista; por defecto "no".
        """
        for lang in props.get("workLanguage") or []:
            code = _LANGUAGE_MAP.get(str(lang).strip())
            if code:
                return code
        return "no"
