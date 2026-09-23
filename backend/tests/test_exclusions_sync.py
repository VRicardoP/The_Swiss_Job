"""Durable projection: versions, restart/retry, rollback and observable lag."""
import asyncio
import uuid

import pytest
from sqlalchemy import select

from models.exclusion_sync_state import ExclusionSyncState
from models.job_filter import JobFilter
from services import exclusions_sync as sync
from tests.conftest import TestSessionLocal
from tests.test_analytics_router import _auth


def _corutina(value):
    async def call(*args, **kwargs): return value
    return call


class _Response:
    status_code = 200
    def __init__(self, version): self.version = version
    def json(self): return {"version": self.version}


class _Core:
    def __init__(self):
        self.version, self.rules, self.fail = 0, [], False
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def put(self, url, json=None):
        if self.fail:
            raise ConnectionError("controlled disconnect")
        if json["version"] > self.version:
            self.version, self.rules = json["version"], json["exclusions"]
        return _Response(self.version)


@pytest.fixture
def core(monkeypatch):
    c = _Core()
    monkeypatch.setattr(sync, "resolve_mode", _corutina("core_primary"))
    monkeypatch.setattr(sync, "resolve_core_profile_id", _corutina(uuid.uuid4()))
    monkeypatch.setattr(sync, "default_client_factory", lambda: c)
    return c


async def _create(client, headers):
    r = await client.post("/api/v1/analytics/filters", headers=headers,
        json={"filter_type": "title_contains", "pattern": "director"})
    assert r.status_code == 201
    return r.json()["id"]


@pytest.mark.asyncio
async def test_no_proyecta_si_el_perfil_sigue_en_local(client, core, monkeypatch):
    headers, _ = await _auth(client)
    monkeypatch.setattr(sync, "resolve_mode", _corutina("local"))
    await _create(client, headers)
    assert core.version == 0
    assert (await client.get("/api/v1/analytics/filters", headers=headers)).json()["sync_status"]["pending"]


@pytest.mark.asyncio
async def test_proyecta_el_conjunto_COMPLETO_incluida_una_baja(client, core):
    headers, _ = await _auth(client)
    fid = await _create(client, headers)
    assert len(core.rules) == 1 and core.version == 1
    assert (await client.delete(f"/api/v1/analytics/filters/{fid}", headers=headers)).status_code == 204
    assert core.rules == [] and core.version == 2
    assert not (await client.get("/api/v1/analytics/filters", headers=headers)).json()["sync_status"]["pending"]


@pytest.mark.asyncio
async def test_un_core_caido_no_tumba_la_operacion_del_usuario(client, core):
    headers, uid = await _auth(client)
    fid = await _create(client, headers)
    core.fail = True
    assert (await client.delete(f"/api/v1/analytics/filters/{fid}", headers=headers)).status_code == 204
    status = (await client.get("/api/v1/analytics/filters", headers=headers)).json()["sync_status"]
    assert status["pending"] and status["version"] == 2
    assert len(core.rules) == 1
    # New session, no new user edit: the persistent drain recovers the deletion.
    core.fail = False
    await sync.drain_pending_exclusions(TestSessionLocal)
    assert core.rules == [] and core.version == 2
    async with TestSessionLocal() as s:
        assert not (await sync.exclusion_sync_status(s, uid))["pending"]


@pytest.mark.asyncio
async def test_old_delivery_cannot_restore_a_deleted_rule(client, core, monkeypatch):
    headers, uid = await _auth(client)
    entered, release = asyncio.Event(), asyncio.Event()
    original = core.put

    async def delayed(url, json=None):
        if json["version"] == 1:
            entered.set()
            await release.wait()
        return await original(url, json=json)
    monkeypatch.setattr(core, "put", delayed)
    create = asyncio.create_task(_create(client, headers))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        async with TestSessionLocal() as s:
            fid = (await s.execute(select(JobFilter.id).where(JobFilter.user_id == uid))).scalar_one()
        assert (await client.delete(f"/api/v1/analytics/filters/{fid}", headers=headers)).status_code == 204
        assert core.version == 2 and core.rules == []
    finally:
        release.set()
        await create
    assert core.rules == [] and core.version == 2
    async with TestSessionLocal() as s:
        assert not (await sync.exclusion_sync_status(s, uid))["pending"]


@pytest.mark.asyncio
async def test_rule_and_pending_snapshot_rollback_together(client):
    _, uid = await _auth(client)
    async with TestSessionLocal() as s:
        await sync.lock_filter_writer(s, uid)
        s.add(JobFilter(user_id=uid, filter_type="title_contains", pattern="director"))
        await sync.queue_exclusions(s, uid)
        await s.rollback()
    async with TestSessionLocal() as s:
        assert (await s.execute(select(JobFilter.id).where(JobFilter.user_id == uid))).first() is None
        assert (await s.execute(select(ExclusionSyncState.user_id).where(ExclusionSyncState.user_id == uid))).first() is None
