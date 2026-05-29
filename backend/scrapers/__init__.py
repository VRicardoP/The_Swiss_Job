"""Scraper registry: discover and instantiate all HTML-scraping providers."""

from scrapers.gastrojob import GastrojobScraper
from scrapers.schuljobs import SchulJobsScraper
from scrapers.stelle_admin import StelleAdminScraper
from scrapers.swiss_schools_inspired import SwissSchoolsInspiredScraper
from scrapers.swiss_schools_isp import SwissSchoolsISPScraper
from scrapers.swiss_schools_nae import SwissSchoolsNAEScraper
from scrapers.tes import TESScraper
from services.job_service import BaseJobProvider

_SCRAPER_CLASSES: dict[str, type[BaseJobProvider]] = {
    "gastrojob": GastrojobScraper,
    "stelle_admin": StelleAdminScraper,
    "tes": TESScraper,
    "schuljobs": SchulJobsScraper,
    # Watchlist de colegios suizos (Fase 1): NAE central + ISP Workday + Inspired SF
    "swiss_schools_nae": SwissSchoolsNAEScraper,
    "swiss_schools_isp": SwissSchoolsISPScraper,
    "swiss_schools_inspired": SwissSchoolsInspiredScraper,
}


def get_all_scrapers() -> list[BaseJobProvider]:
    """Return instances of all registered scrapers."""
    return [cls() for cls in _SCRAPER_CLASSES.values()]


def get_scraper(name: str) -> BaseJobProvider | None:
    """Return a single scraper instance by name, or None."""
    cls = _SCRAPER_CLASSES.get(name)
    return cls() if cls else None


def get_scraper_names() -> list[str]:
    """Return all registered scraper names."""
    return list(_SCRAPER_CLASSES.keys())
