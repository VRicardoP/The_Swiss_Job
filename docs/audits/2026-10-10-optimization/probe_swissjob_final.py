#!/usr/bin/env python3
"""Read-only final SwissJob authority, profile delivery and source gate."""

import asyncio
import importlib
import importlib.metadata
import importlib.util
import json
import os

from sqlalchemy import func, select

from config import settings
from database import async_session
from models.jobhunt_profile_map import JobhuntProfileMap
from models.profile_sync_state import ProfileSyncState
from models.user import User
from services.catalog import CoreCatalog, resolve_catalog
from services.matching import CoreMatching, resolve_matching
from services.profiles import LocalProfile, resolve_profiles
from services.schools import CoreSchools, resolve_schools


LOCAL_PRODUCER_MODULES = {
    "providers",
    *(
        f"providers.{name}"
        for name in (
            "adzuna",
            "authenticjobs",
            "careerjet",
            "dailyremote",
            "himalayas",
            "ictjobs",
            "ilojobs",
            "impactpool",
            "jobicy",
            "jooble",
            "jsearch",
            "proz",
            "reliefweb",
            "remoteco",
            "remoteok",
            "restricted",
            "swisstechjobs",
            "translatorscafe",
            "undpjobs",
            "untalent",
        )
    ),
    *(
        f"scrapers.{name}"
        for name in (
            "gastrojob",
            "medjobs",
            "myscience",
            "schuljobs",
            "stelle_admin",
            "swiss_schools_base",
            "swiss_schools_ecolint",
            "swiss_schools_hautlac",
            "swiss_schools_inspired",
            "swiss_schools_isb",
            "swiss_schools_iscs",
            "swiss_schools_isp",
            "swiss_schools_nae",
            "swiss_schools_zis",
            "tes",
        )
    ),
    "services.harvest_window",
    "services.schools.producer",
    "tasks.fetch_tasks",
    "tasks.harvest_persist",
    "tasks.pipeline_tasks",
    "tasks.scraping_tasks",
    # Helpers and ORM mappings whose only runtime consumers are the local
    # provider/scraper/task stack. Keeping them would preserve a second,
    # unreachable harvesting implementation after the Core cutover.
    "services.compliance",
    "services.circuit_breaker",
    "services.crawler_budget",
    "services.cursor_store",
    "services.data_normalizer",
    "services.deduplicator",
    "services.job_classifier",
    "services.job_repository",
    "services.job_service",
    "services.scraper_engine",
    "services.scraper_stealth",
    "services.source_health",
    "models.source_compliance",
    "models.source_cursor",
    "models.source_health",
    "utils.dates",
    "utils.fetch_diagnostics",
    "utils.http",
}
PORTAL_CREDENTIAL_NAMES = {
    "ADZUNA_APP_ID",
    "ADZUNA_APP_KEY",
    "CAREERJET_AFFID",
    "GLASSDOOR_PARTNER_KEY",
    "INDEED_PARTNER_KEY",
    "JOBCLOUD_PARTNER_API_KEY",
    "JOOBLE_API_KEY",
    "JSEARCH_RAPIDAPI_KEY",
    "LINKEDIN_PARTNER_TOKEN",
    "XING_PARTNER_TOKEN",
}
FORBIDDEN_DISTRIBUTIONS = {"beautifulsoup4", "playwright"}

# Historical modules already removed before DT-144 must stay absent too.
PREVIOUSLY_RETIRED_MODULES = {
    *(
        f"providers.{name}"
        for name in (
            "arbeitnow",
            "euremotejobs",
            "globaljobs",
            "jobgether",
            "jobspresso",
            "nav",
            "ostjob",
            "publicjobs",
            "remotive",
            "thehub",
            "weworkremotely",
            "workingnomads",
            "zebis",
            "zentraljob",
        )
    ),
    "scrapers.financejobs",
    "scrapers.irishjobs",
    "services.legacy_sources",
}


def _module_exists(module: str) -> bool:
    """Return whether a module is importable even if its parent was removed."""
    try:
        return importlib.util.find_spec(module) is not None
    except (AttributeError, ModuleNotFoundError, ValueError):
        return False


def _registry_names(module: str, getter: str) -> set[str]:
    """Read a legacy registry only while it still exists during the cutover."""
    if not _module_exists(module):
        return set()
    package = importlib.import_module(module)
    resolver = getattr(package, getter, None)
    return set(resolver()) if resolver else set()


def _installed(distribution: str) -> bool:
    try:
        importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


async def main() -> int:
    provider_names = _registry_names("providers", "get_provider_names")
    scraper_names = _registry_names("scrapers", "get_scraper_names")
    school_scrapers = {
        name for name in scraper_names if name.startswith("swiss_schools_")
    }

    async with async_session() as session:
        active_ids = list(
            (await session.execute(select(User.id).where(User.is_active.is_(True))))
            .scalars()
            .all()
        )
        linked = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(User)
                    .join(JobhuntProfileMap, JobhuntProfileMap.user_id == User.id)
                    .where(User.is_active.is_(True))
                )
            ).scalar_one()
        )
        pending_snapshots = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ProfileSyncState)
                    .join(User, User.id == ProfileSyncState.user_id)
                    .where(
                        User.is_active.is_(True),
                        ProfileSyncState.delivered_version < ProfileSyncState.version,
                    )
                )
            ).scalar_one()
        )
        snapshot_errors = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(ProfileSyncState)
                    .join(User, User.id == ProfileSyncState.user_id)
                    .where(
                        User.is_active.is_(True),
                        ProfileSyncState.last_error.is_not(None),
                    )
                )
            ).scalar_one()
        )
        sample_user = active_ids[0] if active_ids else None
        authorities = {
            "catalog": type(await resolve_catalog(session)).__name__,
            "matching": type(await resolve_matching(session, sample_user)).__name__,
            "profiles": type(await resolve_profiles(session, sample_user)).__name__,
            "schools": type(await resolve_schools(session, sample_user)).__name__,
        }

    present_local_producers = sorted(
        module
        for module in LOCAL_PRODUCER_MODULES | PREVIOUSLY_RETIRED_MODULES
        if _module_exists(module)
    )
    portal_credentials_present = sorted(PORTAL_CREDENTIAL_NAMES & os.environ.keys())
    forbidden_distributions_present = sorted(
        filter(_installed, FORBIDDEN_DISTRIBUTIONS)
    )
    result = {
        "flags": {
            "profile_sync": settings.CORE_PROFILE_SYNC_ENABLED,
            "feedback": settings.CORE_FEEDBACK_ENABLED,
            "facets": settings.CORE_MATCH_FACETS_ENABLED,
        },
        "active_users": len(active_ids),
        "linked_active_users": linked,
        "pending_snapshots": pending_snapshots,
        "active_snapshot_errors": snapshot_errors,
        "authorities": authorities,
        "registered_providers": sorted(provider_names),
        "registered_scrapers": sorted(scraper_names),
        "school_scrapers": len(school_scrapers),
        "local_producer_modules_present": present_local_producers,
        "portal_credentials_present": portal_credentials_present,
        "forbidden_distributions_present": forbidden_distributions_present,
    }
    failures = []
    if result["flags"] != {"profile_sync": True, "feedback": True, "facets": True}:
        failures.append("core_flags_not_enabled")
    if (
        not active_ids
        or linked != len(active_ids)
        or pending_snapshots
        or snapshot_errors
    ):
        failures.append("active_profile_delivery_incomplete")
    if authorities != {
        "catalog": CoreCatalog.__name__,
        "matching": CoreMatching.__name__,
        "profiles": LocalProfile.__name__,
        "schools": CoreSchools.__name__,
    }:
        failures.append("authority_seams_not_fixed")
    if provider_names:
        failures.append("provider_registry_not_empty")
    if scraper_names or school_scrapers:
        failures.append("scraper_registry_not_empty")
    if present_local_producers:
        failures.append("local_producer_modules_still_importable")
    if portal_credentials_present:
        failures.append("portal_credentials_still_present")
    if forbidden_distributions_present:
        failures.append("local_harvest_dependencies_still_installed")
    result["failures"] = failures
    print(json.dumps(result, sort_keys=True))
    return int(bool(failures))


raise SystemExit(asyncio.run(main()))
