"""Provider registry for sources still owned by SwissJob."""

import logging

from config import settings
from providers.adzuna import AdzunaProvider
from providers.careerjet import CareerjetProvider
from providers.jobicy import JobicyProvider
from providers.jooble import JoobleProvider
from providers.jsearch import JSearchProvider
from providers.proz import ProzProvider
from providers.remoteco import RemoteCoProvider
from providers.restricted import (
    GlassdoorPartnerProvider,
    IndeedPartnerProvider,
    JobCloudPartnerProvider,
    LinkedInAuthorizedProvider,
    XingPartnerProvider,
)
from services.job_service import BaseJobProvider

logger = logging.getLogger(__name__)

_PROVIDER_CLASSES: dict[str, type[BaseJobProvider]] = {
    "adzuna": AdzunaProvider,
    "careerjet": CareerjetProvider,
    "jooble": JoobleProvider,
    "jsearch": JSearchProvider,
    "remoteco": RemoteCoProvider,
    "proz": ProzProvider,
    "jobcloud_partner": JobCloudPartnerProvider,
    "linkedin_authorized": LinkedInAuthorizedProvider,
    "indeed_partner": IndeedPartnerProvider,
    "glassdoor_partner": GlassdoorPartnerProvider,
    "xing_partner": XingPartnerProvider,
    "jobicy": JobicyProvider,
}

_KEY_REQUIREMENTS: dict[str, str] = {
    "adzuna": "ADZUNA_APP_ID",
    "careerjet": "CAREERJET_AFFID",
    "jooble": "JOOBLE_API_KEY",
    "jsearch": "JSEARCH_RAPIDAPI_KEY",
    "jobcloud_partner": "JOBCLOUD_PARTNER_API_KEY",
    "linkedin_authorized": "LINKEDIN_PARTNER_TOKEN",
    "indeed_partner": "INDEED_PARTNER_KEY",
    "glassdoor_partner": "GLASSDOOR_PARTNER_KEY",
    "xing_partner": "XING_PARTNER_TOKEN",
}


def _has_required_key(name: str) -> bool:
    attr = _KEY_REQUIREMENTS.get(name)
    return attr is None or bool(getattr(settings, attr, ""))


def get_provider(name: str) -> BaseJobProvider | None:
    """Get a provider when it is registered and has its required key."""
    cls = _PROVIDER_CLASSES.get(name)
    if cls is None or not _has_required_key(name):
        return None
    return cls()


def get_all_providers() -> list[BaseJobProvider]:
    """Construct every registered provider whose required key is present."""
    return [cls() for name, cls in _PROVIDER_CLASSES.items() if _has_required_key(name)]


def get_provider_names() -> list[str]:
    """Return all registered provider names regardless of key availability."""
    return list(_PROVIDER_CLASSES)


def log_provider_status() -> dict[str, str]:
    """Log availability of the sources SwissJob still owns."""
    status: dict[str, str] = {}
    for name in _PROVIDER_CLASSES:
        key_attr = _KEY_REQUIREMENTS.get(name)
        if key_attr is None:
            status[name] = "enabled"
        elif bool(getattr(settings, key_attr, "")):
            status[name] = "enabled"
        else:
            status[name] = f"disabled (missing {key_attr})"

    enabled = [name for name, state in status.items() if state == "enabled"]
    disabled = {name: state for name, state in status.items() if state != "enabled"}
    logger.info("Provider status: %d/%d enabled %s", len(enabled), len(status), enabled)
    for name, reason in disabled.items():
        logger.warning("Provider '%s': %s", name, reason)
    return status
