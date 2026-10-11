#!/usr/bin/env python3
"""Read-only final SwissJob identity, routing and producer gate."""

import asyncio
import json

from sqlalchemy import func, select

import providers
import scrapers
from config import settings
from database import async_session
from models.jobhunt_profile_map import JobhuntProfileMap
from models.jobhunt_routing import CONSUMER_SWISSJOB, JobhuntRouting, PROFILE_WILDCARD
from models.profile_sync_state import ProfileSyncState
from models.user import User
from services.legacy_sources import disabled_sources
from services.routing import CAPABILITY_MATCHING, MODE_CORE_PRIMARY, resolve_modes


EXPECTED_PROVIDERS = {
    "arbeitnow",
    "euremotejobs",
    "globaljobs",
    "jobgether",
    "jobspresso",
    "nav_arbeidsplassen",
    "ostjob",
    "publicjobs",
    "remotive",
    "thehub",
    "weworkremotely",
    "workingnomads",
    "zebis",
    "zentraljob",
}
EXPECTED_SCRAPERS = {"financejobs", "irishjobs"}
EXPECTED_WILDCARD_CAPABILITIES = {"catalog", "documents", "saved_searches", "schools"}


async def main() -> int:
    configured_providers = disabled_sources(
        settings.LEGACY_DISABLED_PROVIDERS, providers._PROVIDER_CLASSES
    )
    configured_scrapers = disabled_sources(
        settings.LEGACY_DISABLED_SCRAPERS, scrapers._SCRAPER_CLASSES
    )
    provider_status = providers.log_provider_status()
    active_scrapers = set(scrapers._SCRAPER_CLASSES) - configured_scrapers
    school_scrapers = {
        name for name in active_scrapers if name.startswith("swiss_schools_")
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
        matching_modes = await resolve_modes(session, CAPABILITY_MATCHING, active_ids)
        routing_rows = (
            await session.execute(
                select(
                    JobhuntRouting.profile_id,
                    JobhuntRouting.capability,
                    JobhuntRouting.mode,
                ).where(JobhuntRouting.consumer_id == CONSUMER_SWISSJOB)
            )
        ).all()

    wildcard = {
        row.capability: row.mode
        for row in routing_rows
        if row.profile_id == PROFILE_WILDCARD
        and row.capability in EXPECTED_WILDCARD_CAPABILITIES
    }
    in_scope_non_primary = sum(
        1
        for row in routing_rows
        if (
            row.capability in EXPECTED_WILDCARD_CAPABILITIES
            or (row.capability == CAPABILITY_MATCHING and row.profile_id in active_ids)
        )
        and row.mode != MODE_CORE_PRIMARY
    )
    result = {
        "profile_sync_enabled": settings.CORE_PROFILE_SYNC_ENABLED,
        "active_users": len(active_ids),
        "linked_active_users": linked,
        "pending_snapshots": pending_snapshots,
        "active_snapshot_errors": snapshot_errors,
        "matching_core_primary": sum(
            mode == MODE_CORE_PRIMARY for mode in matching_modes.values()
        ),
        "wildcard_modes": wildcard,
        "in_scope_non_primary_rows": in_scope_non_primary,
        "disabled_providers": sorted(configured_providers),
        "disabled_scrapers": sorted(configured_scrapers),
        "disabled_provider_status_count": sum(
            provider_status.get(name) == "disabled (core handover)"
            for name in EXPECTED_PROVIDERS
        ),
        "retained_school_scrapers": sorted(school_scrapers),
    }
    failures = []
    if settings.CORE_PROFILE_SYNC_ENABLED is not True:
        failures.append("profile_sync_disabled")
    if len(active_ids) < 2:
        failures.append("active_user_count_below_baseline")
    if linked != len(active_ids) or pending_snapshots or snapshot_errors:
        failures.append("active_profile_delivery_incomplete")
    if any(mode != MODE_CORE_PRIMARY for mode in matching_modes.values()):
        failures.append("active_matching_not_core_primary")
    if wildcard != {
        capability: MODE_CORE_PRIMARY for capability in EXPECTED_WILDCARD_CAPABILITIES
    }:
        failures.append("wildcard_routing_not_core_primary")
    if in_scope_non_primary:
        failures.append("in_scope_non_primary_rows")
    if configured_providers != EXPECTED_PROVIDERS:
        failures.append("disabled_provider_set_mismatch")
    if configured_scrapers != EXPECTED_SCRAPERS:
        failures.append("disabled_scraper_set_mismatch")
    if result["disabled_provider_status_count"] != len(EXPECTED_PROVIDERS):
        failures.append("provider_registry_not_quiesced")
    if len(school_scrapers) != 8:
        failures.append("retained_school_scrapers_not_eight")
    result["failures"] = failures
    print(json.dumps(result, sort_keys=True))
    return int(bool(failures))


raise SystemExit(asyncio.run(main()))
