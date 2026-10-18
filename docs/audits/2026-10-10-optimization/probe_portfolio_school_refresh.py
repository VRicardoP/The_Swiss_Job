#!/usr/bin/env python3
"""Dispatch VIS from Portfolio while allowing HTTP traffic only to Core.

Run through stdin inside ``portfolio_backend`` after Core and its VIS scope are
live.  This is intentionally a real dispatch: the following Core probe must
observe the resulting successful scope state.
"""

import asyncio
import json

import httpx

from config import settings
from database import AsyncSessionLocal
from routers.schools import refresh_one


async def main() -> int:
    core_host = httpx.URL(settings.CORE_API_BASE_URL).host
    contacted_hosts = []
    original_request = httpx.AsyncClient.request

    async def core_only(self, method, url, **kwargs):
        destination = self.base_url.join(url).host
        contacted_hosts.append(destination)
        if destination != core_host:
            raise AssertionError(
                f"school refresh attempted non-Core HTTP: {destination}"
            )
        return await original_request(self, method, url, **kwargs)

    httpx.AsyncClient.request = core_only
    try:
        async with AsyncSessionLocal() as session:
            response = await refresh_one("vis", db=session, current_user=object())
    finally:
        httpx.AsyncClient.request = original_request

    result = {
        "response": response,
        "core_host": core_host,
        "contacted_hosts": contacted_hosts,
        "only_core_contacted": bool(contacted_hosts)
        and set(contacted_hosts) == {core_host},
    }
    result["failures"] = (
        []
        if response == {"status": "scheduled"} and result["only_core_contacted"]
        else ["school_refresh_not_exclusively_delegated_to_core"]
    )
    print(json.dumps(result, sort_keys=True))
    return int(bool(result["failures"]))


raise SystemExit(asyncio.run(main()))
