"""The saved-search seam preserves the UI and switches BOTH executor entrypoints."""

from contextlib import asynccontextmanager
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import func, select

from config import settings
from models.jobhunt_profile_map import JobhuntProfileMap
from models.jobhunt_routing import CONSUMER_SWISSJOB, PROFILE_WILDCARD, JobhuntRouting
from models.saved_search import SavedSearch
from services import saved_searches as adapter
from tests.conftest import TestSessionLocal
from tests.test_integration_inbox import owner


def item(pid, sid):
    return {"id": str(sid), "profile_id": str(pid), "name": "Python", "filters": {"q": "Python"},
            "min_score": 0, "notify_frequency": "daily", "notify_push": True, "is_active": True,
            "last_run_at": None, "total_matches": 2, "created_at": "2026-09-01T00:00:00Z"}


async def enable(db, uid, mode="core_primary"):
    row = await db.get(JobhuntRouting, (CONSUMER_SWISSJOB, uid, adapter.CAPABILITY))
    if row is None:
        db.add(JobhuntRouting(consumer_id=CONSUMER_SWISSJOB, profile_id=uid, capability=adapter.CAPABILITY, mode=mode))
    else:
        row.mode = mode
    await db.commit()


async def setup_owner(client, db, monkeypatch):
    token, _, pid = await owner(client, db)
    uid = await db.scalar(select(JobhuntProfileMap.user_id).where(JobhuntProfileMap.core_profile_id == pid))
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "synthetic-core-key")
    await enable(db, uid)
    return {"Authorization": f"Bearer {token}"}, pid, uid


async def test_core_crud_and_manual_run_do_not_write_legacy(client, db_session, monkeypatch):
    headers, pid, uid = await setup_owner(client, db_session, monkeypatch)
    sid = uuid.uuid4()
    row = item(pid, sid)
    requests = []

    def serve(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-core-key"
        if request.url.path.endswith("/run"):
            return httpx.Response(202, json={"status": "dispatched", "search_id": str(sid)})
        if request.method == "DELETE":
            assert request.headers["if-match"] == '"etag"'
            return httpx.Response(204)
        if request.method == "GET" and request.url.path == "/v1/saved-searches":
            return httpx.Response(200, json={"items": [row], "next_cursor": None})
        if request.method == "PUT":
            assert request.headers["if-match"] == '"etag"'
        return httpx.Response(201 if request.method == "POST" else 200, json=row, headers={"etag": '"etag"'})

    monkeypatch.setattr(adapter, "default_client_factory", lambda: httpx.AsyncClient(
        base_url="http://core.invalid/v1", headers={"Authorization": "Bearer synthetic-core-key"},
        transport=httpx.MockTransport(serve)))
    listing = await client.get("/api/v1/searches", headers=headers)
    assert listing.status_code == 200 and listing.json()["total"] == 1
    assert listing.json()["data"][0]["user_id"] == str(uid)
    key = str(uuid.uuid4())
    created = await client.post("/api/v1/searches", headers={**headers, "Idempotency-Key": key}, json={"name": "Python"})
    assert created.status_code == 201, created.text
    import json
    posted = next(r for r in requests if r.method == "POST")
    assert posted.headers["idempotency-key"] == key
    assert json.loads(posted.content)["execution_contract"] == "swissjob-v1"
    assert (await client.put(f"/api/v1/searches/{sid}", headers=headers, json={"name": "Python"})).status_code == 200
    assert (await client.post(f"/api/v1/searches/{sid}/run", headers=headers)).json() == {"status": "dispatched", "search_id": str(sid)}
    assert (await client.delete(f"/api/v1/searches/{sid}", headers=headers)).status_code == 204
    assert await db_session.scalar(select(func.count()).select_from(SavedSearch)) == 0


@pytest.mark.parametrize("broken", ["unavailable", "shape", "owner", "repeat_cursor"])
async def test_core_failure_never_returns_local_data(client, db_session, monkeypatch, broken):
    headers, pid, uid = await setup_owner(client, db_session, monkeypatch)
    db_session.add(SavedSearch(user_id=uid, name="local must not leak"))
    await db_session.commit()
    sid = uuid.uuid4()

    def serve(request):
        row = item(pid if broken != "owner" else uuid.uuid4(), sid)
        if broken == "unavailable":
            return httpx.Response(503)
        if broken == "shape":
            return httpx.Response(200, json={"items": [None]})
        return httpx.Response(200, json={"items": [row], "next_cursor": "loop" if broken == "repeat_cursor" else None})

    monkeypatch.setattr(adapter, "default_client_factory", lambda: httpx.AsyncClient(base_url="http://core.invalid/v1", transport=httpx.MockTransport(serve)))
    response = await client.get("/api/v1/searches", headers=headers)
    assert response.status_code == 503
    assert "local must not leak" not in response.text


@pytest.mark.parametrize("mode,core", [("local", False), ("shadow", False), ("core_read", False), ("core_primary", True), ("rollback_pending", True)])
async def test_routing_is_fresh_and_local_executor_obeys_it(client, db_session, monkeypatch, mode, core):
    import database
    from tasks import search_tasks
    _, pid, uid = await setup_owner(client, db_session, monkeypatch)
    search = SavedSearch(user_id=uid, name="search")
    db_session.add(search)
    await db_session.commit()
    sid = search.id
    assert await adapter.core_owns_searches(db_session, uid)
    await enable(db_session, uid, mode)
    assert await adapter.core_owns_searches(db_session, uid) is core

    @asynccontextmanager
    async def session():
        async with TestSessionLocal() as s:
            yield s

    monkeypatch.setattr(database, "task_session", session)
    execute = AsyncMock(return_value=1)
    monkeypatch.setattr(search_tasks, "_execute_single_search", execute)
    result = await search_tasks._run_single_async(str(sid), str(uid))
    assert result["status"] == ("disabled" if core else "success")
    assert execute.await_count == (0 if core else 1)
    execute.reset_mock()
    await search_tasks._run_saved_searches_async()
    assert execute.await_count == (0 if core else 1)


async def test_explicit_override_beats_wildcard_without_cached_authority(client, db_session, monkeypatch):
    _, _, uid = await setup_owner(client, db_session, monkeypatch)
    await enable(db_session, PROFILE_WILDCARD, "core_primary")
    await enable(db_session, uid, "local")
    assert not await adapter.core_owns_searches(db_session, uid)
    await enable(db_session, uid, "core_primary")
    assert await adapter.core_owns_searches(db_session, uid)
