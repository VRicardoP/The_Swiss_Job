"""Scraper registry: discover and instantiate all HTML-scraping providers."""

from scrapers.schuljobs import SchulJobsScraper
from services.job_service import BaseJobProvider

_SCRAPER_CLASSES: dict[str, type[BaseJobProvider]] = {
    "schuljobs": SchulJobsScraper,
    # medjobs (med-jobs.com) SIGUE deshabilitado: está tras un challenge duro de
    # Cloudflare (/cdn-cgi/challenge-platform) que el Playwright endurecido local
    # NO supera. Requiere un browser stealth remoto de pago vía CDP
    # (settings.SCRAPER_BROWSER_CDP_URL). Reactívalo solo con ese opt-in.
    # Watchlist Fase 1: portales centralizados (NAE + ISP + Inspired)
    # Watchlist Fase 2: Group A propios (ZIS + ISB + Ecolint)
    # Watchlist Fase 3: HTML propio menores (Haut-Lac + ISCS)
    # Riviera, Verbier, ISR quedan como "manual": Riviera no expone jobs
    # en HTML estático; Verbier solo enlaza a TES (ya cubierto); ISR usa
    # SPA AbaServices que requeriría Playwright para ~1 vacante.
}


def get_all_scrapers() -> list[BaseJobProvider]:
    """Construct every registered scraper."""
    return [cls() for cls in _SCRAPER_CLASSES.values()]


def get_scraper(name: str) -> BaseJobProvider | None:
    """Return a single scraper instance by name, or None."""
    cls = _SCRAPER_CLASSES.get(name)
    return cls() if cls else None


def get_scraper_names() -> list[str]:
    """Return all registered scraper names."""
    return list(_SCRAPER_CLASSES.keys())
