"""Single-writer F switch remains off until the frozen migration is verified."""

import uuid

import httpx
import pytest
from sqlalchemy import func, select
from unittest.mock import AsyncMock
from types import SimpleNamespace

from config import settings
from models.match_result import MatchResult
from services.matching import core_client
from services.matching.feedback import CoreFeedback, feedback_writer
from services.match_result_service import MatchResultService
from services.matching.identity import set_profile_link
from tests.test_matching_contract import _register, _match_dto


@pytest.mark.asyncio
async def test_feedback_switch_default_keeps_local_writer(db_session, monkeypatch):
    monkeypatch.setattr(settings, "CORE_FEEDBACK_ENABLED", False)
    assert isinstance(feedback_writer(db_session), MatchResultService)


@pytest.mark.asyncio
async def test_feedback_switch_serves_native_without_job_and_ignores_local_overlay(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "CORE_FEEDBACK_ENABLED", True)
    uid, _ = await _register(client)
    await set_profile_link(db_session, uid, uuid.uuid4())
    item = _match_dto("python_zurich", legacy=False)
    item["state"]["feedback"] = "thumbs_up"
    matcher = core_client.CoreMatching(db_session, client_factory=lambda: None)
    # Devuelve (items, total): el total lo informa el core desde el punto 5,
    # y sin el el consumidor vuelve a contar recorriendo el feed.
    monkeypatch.setattr(
        matcher, "_fetch_full_feed", AsyncMock(return_value=([item], 1))
    )
    rows, total = await matcher.results(uid)
    assert total == 1
    assert rows[0]["match"].job_hash == item["vacancy"]["id"]
    assert rows[0]["match"].feedback == "thumbs_up"
    assert await db_session.scalar(select(func.count()).select_from(MatchResult)) == 0


@pytest.mark.asyncio
async def test_feedback_router_never_falls_back_after_write_failure(
    client, db_session, monkeypatch
):
    from routers import match as router

    monkeypatch.setattr(settings, "CORE_FEEDBACK_ENABLED", True)
    uid, headers = await _register(client)
    await set_profile_link(db_session, uid, uuid.uuid4())

    def factory():
        return httpx.AsyncClient(
            base_url="http://core.test/v1",
            transport=httpx.MockTransport(lambda _: httpx.Response(503)),
        )

    monkeypatch.setattr(router, "feedback_writer", lambda db: CoreFeedback(db, factory))
    path = f"/api/v1/match/{uuid.uuid4()}"
    assert (
        await client.post(
            path + "/feedback", headers=headers, json={"feedback": "thumbs_up"}
        )
    ).status_code == 503
    assert (await client.delete(path + "/feedback", headers=headers)).status_code == 503
    assert (
        await client.post(
            path + "/implicit", headers=headers, json={"action": "opened"}
        )
    ).status_code == 503
    assert await db_session.scalar(select(func.count()).select_from(MatchResult)) == 0


@pytest.mark.asyncio
async def test_saved_route_selects_the_core_feedback_reader_at_composition(
    client, db_session, monkeypatch
):
    from routers import match as router

    monkeypatch.setattr(settings, "CORE_FEEDBACK_ENABLED", True)
    uid, headers = await _register(client)
    saved = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(
        router,
        "feedback_writer",
        lambda db: SimpleNamespace(saved=saved),
    )

    async def forbidden_resolver(*_args):
        raise AssertionError("the Core feedback reader must bypass matching routing")

    monkeypatch.setattr(router, "resolve_matching", forbidden_resolver)
    response = await client.get(
        "/api/v1/match/saved?translate=false",
        headers=headers,
    )
    assert response.status_code == 200
    saved.assert_awaited_once_with(uid, limit=100, offset=0)


@pytest.mark.asyncio
async def test_feedback_freeze_blocks_writes_before_auth_but_not_reads(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "FEEDBACK_WRITES_FROZEN", True)
    path = f"/api/v1/match/{uuid.uuid4()}"
    assert (
        await client.post(path + "/feedback", json={"feedback": "thumbs_up"})
    ).status_code == 503
    assert (await client.delete(path + "/feedback")).status_code == 503
    assert (
        await client.post(path + "/implicit", json={"action": "opened"})
    ).status_code == 503
    # Normal auth still applies to the read; the freeze did not replace it.
    assert (await client.get("/api/v1/match/saved")).status_code in (401, 403)
    assert (await client.get("/health/feedback")).json() == {
        "writes": "frozen",
        "writer": "local",
    }
