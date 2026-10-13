#!/usr/bin/env python3
"""Read-only SwissJob gate after retiring Core-owned runtime."""

import asyncio
import importlib.util
import json
import os

from sqlalchemy import func, select

import providers
import scrapers
from database import async_session
from models.jobhunt_profile_map import JobhuntProfileMap
from models.profile_sync_state import ProfileSyncState
from models.user import User
from services.applications.seam import resolve_applications
from services.catalog.seam import resolve_catalog
from services.documents.seam import resolve_documents
from services.matching.seam import resolve_matching
from services.profiles.seam import resolve_profiles
from services.schools.seam import resolve_schools

EXPECTED_PROVIDERS = {
    "adzuna",
    "careerjet",
    "jooble",
    "jsearch",
    "remoteco",
    "proz",
    "jobcloud_partner",
    "linkedin_authorized",
    "indeed_partner",
    "glassdoor_partner",
    "xing_partner",
    "jobicy",
}
EXPECTED_SCRAPERS = {
    "gastrojob",
    "stelle_admin",
    "tes",
    "schuljobs",
    "myscience",
    "swiss_schools_nae",
    "swiss_schools_isp",
    "swiss_schools_inspired",
    "swiss_schools_zis",
    "swiss_schools_isb",
    "swiss_schools_ecolint",
    "swiss_schools_hautlac",
    "swiss_schools_iscs",
}
RETIRED_MODULES = {
    "services.job_matcher",
    "services.match_service",
    "services.routing",
    "services.search_handover",
    "services.legacy_sources",
    "tasks.embedding_tasks",
    "tasks.matching_tasks",
    "tasks.search_tasks",
    "providers.arbeitnow",
    "providers.ostjob",
    "providers.zentraljob",
    "scrapers.financejobs",
    "scrapers.irishjobs",
}
RETIRED_ENV = {
    "LEGACY_DISABLED_PROVIDERS",
    "LEGACY_DISABLED_SCRAPERS",
    "ROUTING_CACHE_TTL_SECONDS",
    "HF_HOME",
    "SENTENCE_TRANSFORMERS_HOME",
}
EXPECTED_AUTHORITIES = {
    "applications": "services.applications.local.LocalApplications",
    "catalog": "services.catalog.core_client.CoreCatalog",
    "documents": "services.documents.core_client.CoreDocuments",
    "matching": "services.matching.core_client.CoreMatching",
    "profiles": "services.profiles.local.LocalProfile",
    "schools": "services.schools.core_client.CoreSchools",
}


def class_path(value):
    cls = type(value)
    return f"{cls.__module__}.{cls.__name__}"


async def main():
    provider_names = set(providers.get_provider_names())
    scraper_names = set(scrapers.get_scraper_names())
    present_modules = sorted(
        name for name in RETIRED_MODULES if importlib.util.find_spec(name)
    )
    present_env = sorted(RETIRED_ENV.intersection(os.environ))

    async with async_session() as session:
        active_ids = list(
            (
                await session.execute(select(User.id).where(User.is_active.is_(True)))
            ).scalars()
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
        pending = int(
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
        errors = int(
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
        user_id = active_ids[0] if active_ids else None
        authorities = {}
        if user_id is not None:
            authorities = {
                "applications": class_path(
                    await resolve_applications(session, user_id)
                ),
                "catalog": class_path(await resolve_catalog(session, user_id)),
                "documents": class_path(await resolve_documents(session, user_id)),
                "matching": class_path(await resolve_matching(session, user_id)),
                "profiles": class_path(await resolve_profiles(session, user_id)),
                "schools": class_path(await resolve_schools(session, user_id)),
            }

    result = {
        "active_users": len(active_ids),
        "linked_active_users": linked,
        "pending_snapshots": pending,
        "active_snapshot_errors": errors,
        "providers": sorted(provider_names),
        "scrapers": sorted(scraper_names),
        "school_scrapers": sorted(
            name for name in scraper_names if name.startswith("swiss_schools_")
        ),
        "retired_modules_present": present_modules,
        "retired_environment_present": present_env,
        "authorities": authorities,
    }
    failures = []
    if not active_ids or linked != len(active_ids) or pending or errors:
        failures.append("profile_delivery_incomplete")
    if provider_names != EXPECTED_PROVIDERS:
        failures.append("provider_registry_mismatch")
    if scraper_names != EXPECTED_SCRAPERS:
        failures.append("scraper_registry_mismatch")
    if present_modules:
        failures.append("retired_modules_importable")
    if present_env:
        failures.append("retired_environment_present")
    if authorities != EXPECTED_AUTHORITIES:
        failures.append("authority_mismatch")
    result["failures"] = failures
    print(json.dumps(result, sort_keys=True))
    return int(bool(failures))


raise SystemExit(asyncio.run(main()))
