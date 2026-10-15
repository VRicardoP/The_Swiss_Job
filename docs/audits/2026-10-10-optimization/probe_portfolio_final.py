#!/usr/bin/env python3
"""Read-only final Portfolio routing and producer gate."""

import asyncio
import importlib.util
import json
import os

from config import settings
from database import AsyncSessionLocal
from services.job_provider import get_all_providers
from services.routing import MODE_CORE_PRIMARY, read_all_modes_fresh


LOCAL_PRODUCER_MODULES = {
    "adzuna_config",
    "dynamitejobs_config",
    "himalayas_config",
    "ictjobs_config",
    "jobroom_config",
    "jobscout24_config",
    "jsearch_config",
    "remoteok_config",
    "services.adzuna_provider_impl",
    "services.cache_registry",
    "services.dynamitejobs_provider_impl",
    "services.job_cache_updater",
    "services.jobicy_provider_impl",
    "services.jobroom_provider_impl",
    "services.jobscout24_provider_impl",
    "services.jsearch_provider_impl",
    "services.remoteok_provider_impl",
    "services.swissdevjobs_provider_impl",
    "services.wordpress_job_base",
    "swissdevjobs_config",
    "swisstechjobs_config",
}
LOCAL_FALLBACK_MODULES = {
    "services.catalog.composite",
    "services.matching.composite",
}
PORTAL_CREDENTIAL_NAMES = {
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "JSEARCH_RAPIDAPI_KEY",
}
EXPECTED_CAPABILITIES = {
    "applications",
    "catalog",
    "documents",
    "matching",
    "saved_searches",
    "schools",
}


async def main() -> int:
    registered = {provider.get_source_name() for provider in get_all_providers()}
    disabled = set(settings.legacy_disabled_sources)
    present_local_modules = sorted(
        module
        for module in LOCAL_PRODUCER_MODULES | LOCAL_FALLBACK_MODULES
        if importlib.util.find_spec(module) is not None
    )
    portal_credentials_present = sorted(PORTAL_CREDENTIAL_NAMES & os.environ.keys())
    async with AsyncSessionLocal() as session:
        modes = await read_all_modes_fresh(session, sorted(EXPECTED_CAPABILITIES))

    result = {
        "background_schedulers_enabled": settings.background_schedulers_enabled,
        "daily_harvest_enabled": settings.scheduler_daily_harvest_enabled,
        "registered_sources": sorted(registered),
        "disabled_sources": sorted(disabled),
        "local_modules_present": present_local_modules,
        "portal_credentials_present": portal_credentials_present,
        "routing_modes": modes,
    }
    failures = []
    if settings.background_schedulers_enabled is not True:
        failures.append("background_schedulers_disabled")
    if settings.scheduler_daily_harvest_enabled is not False:
        failures.append("daily_harvest_enabled")
    if registered:
        failures.append("local_provider_registry_not_empty")
    if present_local_modules:
        failures.append("local_producer_or_fallback_modules_present")
    if portal_credentials_present:
        failures.append("portal_credentials_still_present")
    if any(values != [MODE_CORE_PRIMARY] for values in modes.values()):
        failures.append("routing_not_exactly_core_primary_without_overrides")
    result["failures"] = failures
    print(json.dumps(result, sort_keys=True))
    return int(bool(failures))


raise SystemExit(asyncio.run(main()))
