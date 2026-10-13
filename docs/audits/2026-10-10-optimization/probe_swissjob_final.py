#!/usr/bin/env python3
"""Read-only final SwissJob authority, profile delivery and source gate."""

import asyncio
import importlib.util
import json

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


EXPECTED_PROVIDERS = {
    "adzuna",
    "careerjet",
    "glassdoor_partner",
    "indeed_partner",
    "jobcloud_partner",
    "jobicy",
    "jooble",
    "jsearch",
    "linkedin_authorized",
    "proz",
    "remoteco",
    "xing_partner",
}
EXPECTED_SCRAPERS = {
    "gastrojob",
    "myscience",
    "schuljobs",
    "stelle_admin",
    "tes",
    "swiss_schools_ecolint",
    "swiss_schools_hautlac",
    "swiss_schools_inspired",
    "swiss_schools_isb",
    "swiss_schools_iscs",
    "swiss_schools_isp",
    "swiss_schools_nae",
    "swiss_schools_zis",
}
RETIRED_MODULES = {
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

    present_retired = sorted(
        module
        for module in RETIRED_MODULES
        if importlib.util.find_spec(module) is not None
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
        "retired_modules_present": present_retired,
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
    if provider_names != EXPECTED_PROVIDERS:
        failures.append("provider_registry_mismatch")
    if scraper_names != EXPECTED_SCRAPERS or len(school_scrapers) != 8:
        failures.append("scraper_registry_mismatch")
    if present_retired:
        failures.append("retired_modules_still_importable")
    result["failures"] = failures
    print(json.dumps(result, sort_keys=True))
    return int(bool(failures))


raise SystemExit(asyncio.run(main()))
