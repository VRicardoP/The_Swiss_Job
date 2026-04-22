"""Provider registry: discover and instantiate all job providers."""

import logging

from config import settings
from services.job_service import BaseJobProvider
from providers.adzuna import AdzunaProvider
from providers.arbeitnow import ArbeitnowProvider
from providers.authenticjobs import AuthenticJobsProvider
from providers.careerjet import CareerjetProvider
from providers.dailyremote import DailyRemoteProvider
from providers.euremotejobs import EURemoteJobsProvider
from providers.himalayas import HimalayasProvider
from providers.ictjobs import ICTJobsProvider
from providers.impactpool import ImpactPoolProvider
from providers.jobicy import JobicyProvider
from providers.jobspresso import JobspressoProvider
from providers.jooble import JoobleProvider
from providers.jsearch import JSearchProvider
from providers.ostjob import OstjobProvider
from providers.proz import ProzProvider
from providers.publicjobs import PublicJobsProvider
from providers.reliefweb import ReliefWebProvider
from providers.remoteco import RemoteCoProvider
from providers.remoteok import RemoteOKProvider
from providers.remotive import RemotiveProvider
from providers.swisstechjobs import SwissTechJobsProvider
from providers.translatorscafe import TranslatorsCafeProvider
from providers.weworkremotely import WeWorkRemotelyProvider
from providers.workingnomads import WorkingNomadsProvider
from providers.zebis import ZebisProvider
from providers.zentraljob import ZentraljobProvider
from providers.globaljobs import GlobalJobsProvider
from providers.untalent import UNTalentProvider
from providers.undpjobs import UNDPJobsProvider
from providers.ilojobs import ILOJobsProvider

logger = logging.getLogger(__name__)

# Registry: source_name → provider class
_PROVIDER_CLASSES: dict[str, type[BaseJobProvider]] = {
    # Agregadores generales
    "adzuna": AdzunaProvider,
    "arbeitnow": ArbeitnowProvider,
    "careerjet": CareerjetProvider,
    "jooble": JoobleProvider,
    "jsearch": JSearchProvider,
    # Portales remote generalistas
    "remotive": RemotiveProvider,
    "weworkremotely": WeWorkRemotelyProvider,
    "workingnomads": WorkingNomadsProvider,
    "dailyremote": DailyRemoteProvider,
    "euremotejobs": EURemoteJobsProvider,
    "remoteco": RemoteCoProvider,
    "jobspresso": JobspressoProvider,
    "authenticjobs": AuthenticJobsProvider,
    # Localización y contenido lingüístico
    "proz": ProzProvider,
    "translatorscafe": TranslatorsCafeProvider,
    # Organismos internacionales y ONGs
    # "reliefweb": ReliefWebProvider,     # API v2 requiere appname registrado en apidoc.reliefweb.int
    # "impactpool": ImpactPoolProvider,   # Feed RSS eliminado (404)
    # Portales suizos
    "ostjob": OstjobProvider,
    "zentraljob": ZentraljobProvider,
    "publicjobs": PublicJobsProvider,
    "zebis": ZebisProvider,
    # Organismos internacionales adicionales
    "globaljobs": GlobalJobsProvider,
    # "untalent": UNTalentProvider,       # Requiere token de autenticación (no es pública)
    # "undpjobs": UNDPJobsProvider,       # Feed RSS devuelve HTML, no XML
    # "ilojobs": ILOJobsProvider,         # Feed WordPress devuelve HTML con entidades inválidas
    # Desactivados temporalmente (tech-only, bajo valor para el perfil actual)
    # "himalayas": HimalayasProvider,
    # "jobicy": JobicyProvider,
    # "remoteok": RemoteOKProvider,
    # "ictjobs": ICTJobsProvider,
    # "swisstechjobs": SwissTechJobsProvider,
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
