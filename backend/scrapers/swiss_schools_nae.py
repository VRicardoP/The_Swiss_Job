"""Scraper para el portal central de Nord Anglia Education.

Cubre 3 colegios de la watchlist:
- Collège Champittet (Nyon)
- Collège Alpin Beau Soleil (Villars-sur-Ollon)
- La Côte International School (Aubonne)

Estrategia:
- Itera sobre los 3 colegios y hace una búsqueda por keyword en
  careers.nordangliaeducation.com/search/?q=<keyword>
- Filtra por nombre exacto del colegio en el campo "School" (customfield3)
  para evitar falsos positivos de la búsqueda fuzzy.
- Categoría fijada a "A" para saltarse la penalización H.
"""

import logging
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag

from scrapers.swiss_schools_config import WatchedSchool, schools_by_strategy
from scrapers.swiss_schools_base import SwissSchoolBaseScraper

logger = logging.getLogger(__name__)

NAE_BASE = "https://careers.nordangliaeducation.com"

# Mapa keyword → nombre canónico tal como aparece en el campo "School" del HTML.
# Se usa para filtrar tras la búsqueda fuzzy y evitar falsos positivos.
_SCHOOL_CANONICAL: dict[str, str] = {
    "champittet": "Collège Champittet",
    "beau soleil": "Collège Beau Soleil",  # NAE lo lista sin "Alpin"
    "la cote": "La Côte International School",
}


class SwissSchoolsNAEScraper(SwissSchoolBaseScraper):
    SOURCE_NAME = "swiss_schools_nae"
    LISTING_URL = f"{NAE_BASE}/search/"
    RATE_LIMIT_SECONDS = 2.0
    MAX_PAGES = 1  # NAE devuelve todos en una sola página
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = False
    PAGE_SIZE = 100

    def __init__(self):
        super().__init__()
        self._schools: list[WatchedSchool] = schools_by_strategy("nae_central")
        self._current_school: WatchedSchool | None = None

    # ------------------------------------------------------------------
    # Override _scrape_with_httpx: itera por colegio en vez de paginar
    # ------------------------------------------------------------------

    async def _scrape_with_httpx(self, query: str) -> list[dict]:
        """Una pasada del flujo base de scraping por cada colegio vigilado.

        Se sobreescribe ESTE punto y no fetch_jobs (VD.4a): así se hereda el
        ciclo de compliance completo del BaseScraper — pre-check, rearme de
        flags del run y la rehabilitación por "vacío verificado". El antiguo
        override de fetch_jobs conservaba `if results:` para rehabilitar, y en
        una watchlist donde 0 vacantes durante meses es lo NORMAL eso dejaba la
        fuente apagada para siempre si caía en el kill-switch.
        """
        all_jobs: list[dict] = []
        # G2/P3-1 + G4/P3-4: las N pasadas comparten `_stop_reason`. Se recoge
        # el de cada colegio y se combinan con la prioridad
        # `error > None > known_page`: el acumulador booleano anterior
        # propagaba el `error` pero borraba el `None` («con hambre»), que es la
        # señal que consume el re-bootstrap del presupuesto en scraping_tasks.
        stop_reasons: list = []
        for school in self._schools:
            self._current_school = school
            self._stop_reason = None
            all_jobs.extend(await super()._scrape_with_httpx(query))
            stop_reasons.append(self._stop_reason)
            # G1/P3-10: los N colegios comparten HOST — si uno reporta
            # bloqueo, seguir con el resto sumaría N reportes en un solo run
            # y alcanzaria el kill-switch (threshold 3) por un episodio
            # transitorio (hasta 24h de silencio). Un bloqueo corta el run.
            if self._run_block_reported:
                break
        self._stop_reason = self.combine_stop_reasons(stop_reasons)
        return all_jobs

    # ------------------------------------------------------------------
    # URL building & parsing
    # ------------------------------------------------------------------

    def build_listing_url(self, page: int, query: str) -> str:
        keyword = (self._current_school.params or {}).get("keyword", "")
        return f"{NAE_BASE}/search/?q={quote_plus(keyword)}"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extrae jobs y filtra estrictamente por nombre del colegio."""
        school = self._current_school
        if not school:
            return []

        keyword = (school.params or {}).get("keyword", "")
        canonical = _SCHOOL_CANONICAL.get(keyword, school.name)

        out: list[dict] = []
        for tile in soup.select("li.job-tile"):
            stub = self._parse_tile(tile, canonical, school)
            if stub:
                out.append(stub)
        return out

    def _parse_tile(
        self, tile: Tag, canonical_school: str, school: WatchedSchool
    ) -> dict | None:
        # Filtrar por colegio (campo "School")
        school_field = self._extract_field(tile, "customfield3")
        if not school_field:
            return None
        if canonical_school.lower() not in school_field.lower():
            return None

        title_a = tile.select_one("a.jobTitle-link")
        if not title_a:
            return None
        title = title_a.get_text(strip=True)
        href = title_a.get("href", "")
        url = f"{NAE_BASE}{href}" if href.startswith("/") else href

        # Source ID = parte numérica final del data-url
        data_url = tile.get("data-url", "")
        source_id = data_url.rstrip("/").split("/")[-1] if data_url else url

        city = self._extract_field(tile, "city") or school.city
        country = self._extract_field(tile, "country") or "CH"

        return {
            "source": self.SOURCE_NAME,
            "source_id": source_id,
            "title": title,
            "company": school.name,  # Nombre del colegio como empresa
            "location": f"{city}, {country}" if city else country,
            "url": url,
            # Categoría real (probablemente H) la asigna el classifier.
            # El bypass se aplica en match_service según watchlist_schools_enabled.
            "tags": ["education", "international school", school.id],
            "language": "en",
        }

    @staticmethod
    def _extract_field(tile: Tag, field_name: str) -> str | None:
        """Lee el contenido de #job-{id}-desktop-section-{field}-value."""
        el = tile.select_one(f'div[id$="-desktop-section-{field_name}-value"]')
        if el:
            return el.get_text(strip=True)
        return None
