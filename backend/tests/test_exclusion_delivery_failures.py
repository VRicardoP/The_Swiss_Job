"""Delivery after local commit: lost ACK, diagnostics outage and version drift."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from services import exclusions_sync as sync
from tests.conftest import TestSessionLocal
from tests.test_analytics_router import _auth
from tests.test_exclusions_sync import core, _create  # noqa: F401  (fixture de pytest: se importa para que la resuelva por nombre)


@pytest.mark.asyncio
@pytest.mark.parametrize("break_rollback", [False, True])
async def test_diagnostics_outage_does_not_undo_success(client, core, monkeypatch, break_rollback):  # noqa: F811  (la fixture, no una redefinición)
    headers, uid = await _auth(client)
    unavailable = False
    execute, rollback = AsyncSession.execute, AsyncSession.rollback

    async def fail_execute(self, *args, **kwargs):
        if unavailable:
            raise ConnectionError("controlled diagnostics outage")
        return await execute(self, *args, **kwargs)

    async def fail_rollback(self, *args, **kwargs):
        if unavailable and break_rollback:
            raise ConnectionError("controlled rollback outage")
        return await rollback(self, *args, **kwargs)

    async def disconnect(*args, **kwargs):
        nonlocal unavailable
        unavailable = True
        raise ConnectionError("controlled transport outage")

    monkeypatch.setattr(AsyncSession, "execute", fail_execute)
    monkeypatch.setattr(AsyncSession, "rollback", fail_rollback)
    monkeypatch.setattr(core, "put", disconnect)
    try:
        await _create(client, headers)  # actual POST must remain 201
    finally:
        unavailable = False
    async with TestSessionLocal() as session:
        status = await sync.exclusion_sync_status(session, uid)
        assert status["pending"] and status["version"] == 1
        assert status["delivered_version"] == 0


@pytest.mark.asyncio
async def test_core_version_ahead_is_observable_not_acknowledged(client, core):  # noqa: F811  (la fixture, no una redefinición)
    headers, uid = await _auth(client)
    core.version = 5
    await _create(client, headers)
    async with TestSessionLocal() as session:
        status = await sync.exclusion_sync_status(session, uid)
    assert status["pending"]
    assert status["last_error"] == "core_version_ahead"
    assert status["delivered_version"] == 0
    assert core.version == 5 and core.rules == []


@pytest.mark.asyncio
async def test_ack_lost_retries_without_another_edit(client, core, monkeypatch):  # noqa: F811  (la fixture, no una redefinición)
    headers, uid = await _auth(client)
    original = core.put
    calls = 0

    async def lose_first_ack(*args, **kwargs):
        nonlocal calls
        calls += 1
        response = await original(*args, **kwargs)
        if calls == 1:
            raise ConnectionError("controlled ACK loss after core commit")
        return response

    monkeypatch.setattr(core, "put", lose_first_ack)
    await _create(client, headers)
    assert core.version == 1 and len(core.rules) == 1
    async with TestSessionLocal() as session:
        assert (await sync.exclusion_sync_status(session, uid))["pending"]
    await sync.drain_pending_exclusions(TestSessionLocal)
    assert calls == 2 and core.version == 1 and len(core.rules) == 1
    async with TestSessionLocal() as session:
        assert not (await sync.exclusion_sync_status(session, uid))["pending"]
