#!/usr/bin/env python3
"""Read-only final SwissJob authority, profile delivery and source gate."""

import asyncio
import importlib.util
import json
import os

from sqlalchemy import func, select

import providers
import scrapers
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


async def main() -> int:
    provider_names = set(providers.get_provider_names())
    scraper_names = set(scrapers.get_scraper_names())
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
        if importlib.util.find_spec(module) is not None
    )
    portal_credentials_present = sorted(PORTAL_CREDENTIAL_NAMES & os.environ.keys())
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
    result["failures"] = failures
    print(json.dumps(result, sort_keys=True))
    return int(bool(failures))


raise SystemExit(asyncio.run(main()))
