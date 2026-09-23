"""A failed in-flight duplicate must not overwrite an already confirmed ACK."""

import asyncio

import pytest
from sqlalchemy import select

from models.exclusion_sync_state import ExclusionSyncState
from services import exclusions_sync as sync
from tests.conftest import TestSessionLocal
from tests.test_analytics_router import _auth
from tests.test_exclusions_sync import core  # noqa: F401  (fixture de pytest: se importa para que la resuelva por nombre)


@pytest.mark.asyncio
async def test_late_duplicate_failure_preserves_success_diagnostics(client, core):  # noqa: F811  (la fixture, no una redefinición)
    _, uid = await _auth(client)
    async with TestSessionLocal() as session:
        await sync.lock_filter_writer(session, uid)
        await sync.queue_exclusions(session, uid)
        await session.commit()

    entered, release = asyncio.Event(), asyncio.Event()
    original = core.put
    calls = 0

    async def put(url, json=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            raise ValueError("core_http_409")
        return await original(url, json=json)

    core.put = put

    async def deliver():
        async with TestSessionLocal() as session:
            return await sync.sync_exclusions_to_core(session, uid)

    late = asyncio.create_task(deliver())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert (await deliver())["status"] == "ok"
    finally:
        release.set()
        await late
    async with TestSessionLocal() as session:
        state = (
            await session.execute(
                select(ExclusionSyncState).where(ExclusionSyncState.user_id == uid)
            )
        ).scalar_one()
        assert state.delivered_version == state.version
        assert state.last_error is None
