"""Provider registry: discover and instantiate all job providers."""

import logging

from config import settings
from services.job_service import BaseJobProvider
from providers.adzuna import AdzunaProvider
from providers.arbeitnow import ArbeitnowProvider
from providers.careerjet import CareerjetProvider
from providers.himalayas import HimalayasProvider
from providers.ictjobs import ICTJobsProvider
from providers.jobicy import JobicyProvider
from providers.jooble import JoobleProvider
from providers.jsearch import JSearchProvider
from providers.ostjob import OstjobProvider
from providers.publicjobs import PublicJobsProvider
from providers.remoteok import RemoteOKProvider
from providers.remotive import RemotiveProvider
from providers.swisstechjobs import SwissTechJobsProvider
from providers.weworkremotely import WeWorkRemotelyProvider
from providers.zebis import ZebisProvider
from providers.zentraljob import ZentraljobProvider

logger = logging.getLogger(__name__)

# Registry: source_name → provider class
_PROVIDER_CLASSES: dict[str, type[BaseJobProvider]] = {
    "adzuna": AdzunaProvider,
    "arbeitnow": ArbeitnowProvider,
    "careerjet": CareerjetProvider,
    "himalayas": HimalayasProvider,
    "ictjobs": ICTJobsProvider,
    "jobicy": JobicyProvider,
    "jooble": JoobleProvider,
    "jsearch": JSearchProvider,
    "ostjob": OstjobProvider,
    "remoteok": RemoteOKProvider,
    "remotive": RemotiveProvider,
    "swisstechjobs": SwissTechJobsProvider,
    "weworkremotely": WeWorkRemotelyProvider,
    "zebis": ZebisProvider,
    "zentraljob": ZentraljobProvider,
    "publicjobs": PublicJobsProvider,
}

# Providers that require API keys — skip instantiation if key is empty
_KEY_REQUIREMENTS: dict[str, str] = {
    "adzuna": "ADZUNA_APP_ID",
    "careerjet": "CAREERJET_AFFID",
    "jooble": "JOOBLE_API_KEY",
    "jsearch": "JSEARCH_RAPIDAPI_KEY",
}


def _has_required_key(name: str) -> bool:
    """Check if the provider's required API key is configured."""
    attr = _KEY_REQUIREMENTS.get(name)
    if attr is None:
        return True  # No key required
    return bool(getattr(settings, attr, ""))


def get_provider(name: str) -> BaseJobProvider | None:
    """Get a single provider instance by source name.

    Returns None if the provider is unknown or its API key is missing.
    """
    cls = _PROVIDER_CLASSES.get(name)
    if cls is None:
        return None
    if not _has_required_key(name):
        return None
    return cls()


def get_all_providers() -> list[BaseJobProvider]:
    """Return instances of all enabled providers (skips those missing API keys)."""
    return [cls() for name, cls in _PROVIDER_CLASSES.items() if _has_required_key(name)]


def get_provider_names() -> list[str]:
    """Return all registered provider names (regardless of key availability)."""
    return list(_PROVIDER_CLASSES.keys())


def log_provider_status() -> dict[str, str]:
    """Log which providers are enabled/disabled and why. Returns status dict."""
    status: dict[str, str] = {}
    for name in _PROVIDER_CLASSES:
        key_attr = _KEY_REQUIREMENTS.get(name)
        if key_attr is None:
            status[name] = "enabled"
        elif bool(getattr(settings, key_attr, "")):
            status[name] = "enabled"
        else:
            status[name] = f"disabled (missing {key_attr})"

    enabled = [n for n, s in status.items() if s == "enabled"]
    disabled = {n: s for n, s in status.items() if s != "enabled"}

    logger.info(
        "Provider status: %d/%d enabled %s",
        len(enabled),
        len(status),
        enabled,
    )
    for name, reason in disabled.items():
        logger.warning("Provider '%s': %s", name, reason)

    return status
