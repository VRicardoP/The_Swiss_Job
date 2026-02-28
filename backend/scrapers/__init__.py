"""Scraper registry: discover and instantiate all HTML-scraping providers."""

from scrapers.financejobs import FinancejobsScraper
from scrapers.gastrojob import GastrojobScraper
from scrapers.medjobs import MedJobsScraper
from scrapers.myscience import MyScienceScraper
from scrapers.stelle_admin import StelleAdminScraper
from services.scraper_engine import BaseScraper

_SCRAPER_CLASSES: dict[str, type[BaseScraper]] = {
    "myscience": MyScienceScraper,
    "gastrojob": GastrojobScraper,
    "financejobs": FinancejobsScraper,
    "medjobs": MedJobsScraper,
    "stelle_admin": StelleAdminScraper,
}


def get_all_scrapers() -> list[BaseScraper]:
    """Return instances of all registered scrapers."""
    return [cls() for cls in _SCRAPER_CLASSES.values()]


def get_scraper(name: str) -> BaseScraper | None:
    """Return a single scraper instance by name, or None."""
    cls = _SCRAPER_CLASSES.get(name)
    return cls() if cls else None


def get_scraper_names() -> list[str]:
    """Return all registered scraper names."""
    return list(_SCRAPER_CLASSES.keys())
