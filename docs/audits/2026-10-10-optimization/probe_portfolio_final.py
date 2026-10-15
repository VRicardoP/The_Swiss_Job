#!/usr/bin/env python3
"""Read-only final Portfolio routing and producer gate."""

import asyncio
import importlib.metadata
import importlib.util
import json
import os

from config import settings
from database import AsyncSessionLocal
from services.routing import MODE_CORE_PRIMARY, read_all_modes_fresh


LOCAL_PRODUCER_MODULES = {
    *(
        f"routers.{name}"
        for name in (
            "adzuna_jobs",
            "dynamitejobs_jobs",
            "himalayas_jobs",
            "ictjobs_jobs",
            "jobroom_jobs",
            "jobscout24_jobs",
            "remoteok_jobs",
            "swissdevjobs_jobs",
            "swisstechjobs_jobs",
        )
    ),
    "adzuna_config",
    "dynamitejobs_config",
    "himalayas_config",
    "ictjobs_config",
    "jobroom_config",
    "jobscout24_config",
    "jsearch_config",
    "remoteok_config",
    "services.adzuna_provider_impl",
    "services.analysis_runner",
    "services.cache_registry",
    "services.dynamitejobs_provider_impl",
    "services.job_cache_updater",
    "services.job_router_helpers",
    "services.jobicy_provider_impl",
    "services.jobroom_provider_impl",
    "services.jobscout24_provider_impl",
    "services.jsearch_provider_impl",
    "services.remoteok_provider_impl",
    "services.swissdevjobs_provider_impl",
    "services.worker_bridge",
    "services.wordpress_job_base",
    "services.circuit_breaker",
    "services.job_matcher",
    "services.job_provider",
    "swissdevjobs_config",
    "swisstechjobs_config",
}


def _module_exists(module: str) -> bool:
    """Return whether a module is importable even if its parent was removed."""
    try:
        return importlib.util.find_spec(module) is not None
    except (AttributeError, ModuleNotFoundError, ValueError):
        return False


def _installed(distribution: str) -> bool:
    try:
        importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


LOCAL_FALLBACK_MODULES = {
    "services.catalog.composite",
    "services.catalog.local",
    "services.matching.composite",
    "services.matching.local",
}
PORTAL_CREDENTIAL_NAMES = {
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "JSEARCH_RAPIDAPI_KEY",
}
FORBIDDEN_DISTRIBUTIONS = {"numpy", "scikit-learn", "sentence-transformers", "torch"}
EXPECTED_CAPABILITIES = {
    "applications",
    "catalog",
    "documents",
    "matching",
    "saved_searches",
    "schools",
}


async def main() -> int:
    disabled = set(settings.legacy_disabled_sources)
    present_local_modules = sorted(
        module
        for module in LOCAL_PRODUCER_MODULES | LOCAL_FALLBACK_MODULES
        if _module_exists(module)
    )
    portal_credentials_present = sorted(PORTAL_CREDENTIAL_NAMES & os.environ.keys())
    forbidden_distributions_present = sorted(filter(_installed, FORBIDDEN_DISTRIBUTIONS))
    async with AsyncSessionLocal() as session:
        modes = await read_all_modes_fresh(session, sorted(EXPECTED_CAPABILITIES))

    result = {
        "background_schedulers_enabled": settings.background_schedulers_enabled,
        "daily_harvest_enabled": settings.scheduler_daily_harvest_enabled,
        "disabled_sources": sorted(disabled),
        "local_modules_present": present_local_modules,
        "portal_credentials_present": portal_credentials_present,
        "forbidden_distributions_present": forbidden_distributions_present,
        "routing_modes": modes,
    }
    failures = []
    if settings.background_schedulers_enabled is not True:
        failures.append("background_schedulers_disabled")
    if settings.scheduler_daily_harvest_enabled is not False:
        failures.append("daily_harvest_enabled")
    if present_local_modules:
        failures.append("local_producer_or_fallback_modules_present")
    if portal_credentials_present:
        failures.append("portal_credentials_still_present")
    if forbidden_distributions_present:
        failures.append("local_matching_dependencies_still_installed")
    if any(values != [MODE_CORE_PRIMARY] for values in modes.values()):
        failures.append("routing_not_exactly_core_primary_without_overrides")
    result["failures"] = failures
    print(json.dumps(result, sort_keys=True))
    return int(bool(failures))


raise SystemExit(asyncio.run(main()))
