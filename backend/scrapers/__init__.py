"""Scraper registry: discover and instantiate all HTML-scraping providers."""

from scrapers.financejobs import FinancejobsScraper
from scrapers.gastrojob import GastrojobScraper
from scrapers.myscience import MyScienceScraper
from scrapers.schuljobs import SchulJobsScraper
from scrapers.stelle_admin import StelleAdminScraper
from scrapers.swiss_schools_ecolint import SwissSchoolsEcolintScraper
from scrapers.swiss_schools_hautlac import SwissSchoolsHautLacScraper
from scrapers.swiss_schools_inspired import SwissSchoolsInspiredScraper
from scrapers.swiss_schools_isb import SwissSchoolsISBScraper
from scrapers.swiss_schools_iscs import SwissSchoolsISCSScraper
from scrapers.swiss_schools_isp import SwissSchoolsISPScraper
from scrapers.swiss_schools_nae import SwissSchoolsNAEScraper
from scrapers.swiss_schools_zis import SwissSchoolsZISScraper
from scrapers.tes import TESScraper
from services.job_service import BaseJobProvider

_SCRAPER_CLASSES: dict[str, type[BaseJobProvider]] = {
    "gastrojob": GastrojobScraper,
    "stelle_admin": StelleAdminScraper,
    "tes": TESScraper,
    "schuljobs": SchulJobsScraper,
    # Reactivados gracias a la capa anti-detección (scraper_stealth): con las
    # cabeceras realistas (client hints + Sec-Fetch) ambos vuelven a devolver
    # HTTP 200 con datos. Sonda en vivo: myscience ~14 jobs/pág, financejobs ~10.
    "myscience": MyScienceScraper,
    "financejobs": FinancejobsScraper,
    # medjobs (med-jobs.com) SIGUE deshabilitado: está tras un challenge duro de
    # Cloudflare (/cdn-cgi/challenge-platform) que el Playwright endurecido local
    # NO supera. Requiere un browser stealth remoto de pago vía CDP
    # (settings.SCRAPER_BROWSER_CDP_URL). Reactívalo solo con ese opt-in.
    # Watchlist Fase 1: portales centralizados (NAE + ISP + Inspired)
    "swiss_schools_nae": SwissSchoolsNAEScraper,
    "swiss_schools_isp": SwissSchoolsISPScraper,
    "swiss_schools_inspired": SwissSchoolsInspiredScraper,
    # Watchlist Fase 2: Group A propios (ZIS + ISB + Ecolint)
    "swiss_schools_zis": SwissSchoolsZISScraper,
    "swiss_schools_isb": SwissSchoolsISBScraper,
    "swiss_schools_ecolint": SwissSchoolsEcolintScraper,
    # Watchlist Fase 3: HTML propio menores (Haut-Lac + ISCS)
    # Riviera, Verbier, ISR quedan como "manual": Riviera no expone jobs
    # en HTML estático; Verbier solo enlaza a TES (ya cubierto); ISR usa
    # SPA AbaServices que requeriría Playwright para ~1 vacante.
    "swiss_schools_hautlac": SwissSchoolsHautLacScraper,
    "swiss_schools_iscs": SwissSchoolsISCSScraper,
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
