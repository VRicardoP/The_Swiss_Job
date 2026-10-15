"""Scraper registry: the HTML-scraping providers this deployment still owns.

EMPTY since the DT-144 handover: the thirteen scrapers that lived here are
harvested by jobhunt-core, and their modules stay in the tree as the rollback
path. `scripts/check_source_ownership.py` fails CI if one comes back without an
explicit decision.

Two sources were never in here and the reason still stands:

- medjobs (med-jobs.com) sits behind a hard Cloudflare challenge
  (/cdn-cgi/challenge-platform) that the local hardened Playwright does not
  pass. It would need a paid remote stealth browser via
  `settings.SCRAPER_BROWSER_CDP_URL`.
- Riviera, Verbier and ISR stay manual: Riviera publishes no jobs in static
  HTML, Verbier only links to TES (already covered), and ISR uses an
  AbaServices SPA that would need a browser for about one vacancy.
"""

from services.job_service import BaseJobProvider

_SCRAPER_CLASSES: dict[str, type[BaseJobProvider]] = {}


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
