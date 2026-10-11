#!/usr/bin/env python3
"""Read-only final Portfolio routing and producer gate."""

import asyncio
import json

from config import settings
from database import AsyncSessionLocal
from services.job_provider import get_all_providers
from services.routing import MODE_CORE_PRIMARY, read_all_modes_fresh


EXPECTED_DISABLED = {
    "arbeitnow",
    "irishjobs",
    "jobgether",
    "nav",
    "ostjob",
    "remotive",
    "thehub",
    "weworkremotely",
    "zentraljob",
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
    owned = registered - disabled
    async with AsyncSessionLocal() as session:
        modes = await read_all_modes_fresh(session, sorted(EXPECTED_CAPABILITIES))

    result = {
        "background_schedulers_enabled": settings.background_schedulers_enabled,
        "registered_sources": sorted(registered),
        "disabled_sources": sorted(disabled),
        "owned_sources": sorted(owned),
        "routing_modes": modes,
    }
    failures = []
    if settings.background_schedulers_enabled is not True:
        failures.append("background_schedulers_disabled")
    if disabled != EXPECTED_DISABLED:
        failures.append("disabled_source_set_mismatch")
    if owned != registered - EXPECTED_DISABLED or len(owned) != 11:
        failures.append("retained_source_set_mismatch")
    if any(values != [MODE_CORE_PRIMARY] for values in modes.values()):
        failures.append("routing_not_exactly_core_primary_without_overrides")
    result["failures"] = failures
    print(json.dumps(result, sort_keys=True))
    return int(bool(failures))


raise SystemExit(asyncio.run(main()))
