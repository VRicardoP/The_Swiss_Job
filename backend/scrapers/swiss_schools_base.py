"""Base común de los scrapers de colegios suizos (watchlist).

Todos los scrapers `swiss_schools_*` que extraen de un listado sin página de
detalle comparten `FETCH_DETAILS=False`, un `parse_job_detail` no-op y el mismo
`normalize_job` (el stub del listado ya viene en el esquema unificado; solo se
añade el hash). Esta base elimina esa duplicación byte-idéntica; cada subclase
implementa únicamente su `parse_listing_page`/`_parse_*` específico.

NOTA: swiss_schools_isp NO usa esta base — su normalize_job tiene lógica propia.
"""

from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper


class SwissSchoolBaseScraper(BaseScraper):
    """Scraper de colegio suizo: listado sin detalle, normalize solo añade hash."""

    FETCH_DETAILS = False
    # Watchlist: un colegio publica una vacante cada varios meses, así que el
    # backoff por "runs sin novedades" los aparcaría PERMANENTEMENTE — justo las
    # fuentes cuyo evento raro es el más valioso para el perfil objetivo
    # (docencia). Decisión del propietario 2026-08-06; ver CrawlerBudgetService.
    WATCHLIST_SOURCE = True

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        """Sin uso — FETCH_DETAILS=False."""
        return {}

    def normalize_job(self, raw: dict) -> dict:
        """El stub ya viene en el esquema unificado; solo añadimos el hash."""
        raw["hash"] = self.compute_hash(raw["title"], raw["company"], raw["url"])
        return raw
