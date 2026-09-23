"""Delivery ACK requires a durable notification; replay never duplicates it."""

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from config import settings
from main import app
from models.integration_inbox import IntegrationInbox
from models.notification import Notification
from tests.test_integration_inbox import URL, HEADERS, event, owner


def search_event(pid, *, push=True):
    body = event(pid)
    sid = str(uuid.uuid4())
    body["event"].update(
        type="saved_search.matches",
        aggregate="saved_search",
        aggregate_id=sid,
        payload={
            "search_id": sid,
            "profile_id": str(pid),
            "search_name": "S" * 200,
            "match_count": 3,
            "notify_push": push,
        },
    )
    return body


@pytest.mark.parametrize("push", [False, True])
async def test_search_notification_committed_once_before_sse(
    client, db_session, monkeypatch, push
):
    monkeypatch.setattr(settings, "CORE_INBOX_TOKEN", "synthetic-inbox-token")
    _, _, pid = await owner(client, db_session)
    calls = []

    async def broadcast(uid, kind, data):
        # A distinct session must already see the notification when SSE fires.
        from tests.conftest import TestSessionLocal

        async with TestSessionLocal() as s:
            row = await s.get(Notification, uuid.UUID(data["notification_id"]))
            assert row is not None and row.user_id == uid
        calls.append((kind, data))

    monkeypatch.setattr(
        app.state,
        "sse_manager",
        SimpleNamespace(broadcast_to_user=broadcast),
        raising=False,
    )
    body = search_event(pid, push=push)
    for inserted in (True, False):
        response = await client.post(URL, json=body, headers=HEADERS)
        assert response.status_code == 202, response.text
        assert response.json()["inserted"] is inserted
    rows = (await db_session.scalars(select(Notification))).all()
    assert len(rows) == 1
    assert rows[0].event_type == "new_matches" and len(rows[0].title) <= 200
    assert rows[0].data == {
        "search_id": body["event"]["aggregate_id"],
        "search_name": "S" * 200,
        "match_count": 3,
    }
    assert len(calls) == int(push)


@pytest.mark.parametrize(
    "broken", ["owner", "search", "version", "count", "name", "nul", "push"]
)
async def test_malformed_search_event_has_no_receipt(
    client, db_session, monkeypatch, broken
):
    monkeypatch.setattr(settings, "CORE_INBOX_TOKEN", "synthetic-inbox-token")
    _, _, pid = await owner(client, db_session)
    body = search_event(pid)
    e, p = body["event"], body["event"]["payload"]
    if broken == "owner":
        e["subject_profile_id"] = None
    elif broken == "search":
        p["search_id"] = str(uuid.uuid4())
    elif broken == "version":
        e["version"] = 2
    elif broken == "count":
        p["match_count"] = True
    elif broken == "name":
        p["search_name"] = "x" * 201
    elif broken == "nul":
        p["search_name"] = "x\x00"
    else:
        p["notify_push"] = "false"
    assert (await client.post(URL, json=body, headers=HEADERS)).status_code == 422
    assert (
        await db_session.scalar(select(func.count()).select_from(IntegrationInbox)) == 0
    )
    assert await db_session.scalar(select(func.count()).select_from(Notification)) == 0


async def test_sse_failure_does_not_undo_notification_or_duplicate_replay(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "CORE_INBOX_TOKEN", "synthetic-inbox-token")
    _, _, pid = await owner(client, db_session)
    broadcast = AsyncMock(side_effect=RuntimeError("synthetic Redis outage"))
    monkeypatch.setattr(
        app.state,
        "sse_manager",
        SimpleNamespace(broadcast_to_user=broadcast),
        raising=False,
    )
    body = search_event(pid)
    assert (await client.post(URL, json=body, headers=HEADERS)).status_code == 202
    assert (await client.post(URL, json=body, headers=HEADERS)).json()[
        "inserted"
    ] is False
    assert await db_session.scalar(select(func.count()).select_from(Notification)) == 1
    assert broadcast.await_count == 1
    changed = copy.deepcopy(body)
    changed["event"]["payload"]["match_count"] = 4
    assert (await client.post(URL, json=changed, headers=HEADERS)).status_code == 409


async def test_failed_notification_rolls_back_receipt_and_retry_creates_it(
    client, db_session, monkeypatch
):
    from routers import integration_inbox

    monkeypatch.setattr(settings, "CORE_INBOX_TOKEN", "synthetic-inbox-token")
    _, _, pid = await owner(client, db_session)
    body = search_event(pid, push=False)

    def broken_projection(**kwargs):
        raise RuntimeError("synthetic notification write failure")

    with monkeypatch.context() as failing:
        failing.setattr(integration_inbox, "Notification", broken_projection)
        with pytest.raises(RuntimeError, match="synthetic notification"):
            await client.post(URL, json=body, headers=HEADERS)
    assert (
        await db_session.scalar(select(func.count()).select_from(IntegrationInbox)) == 0
    )
    assert await db_session.scalar(select(func.count()).select_from(Notification)) == 0
    response = await client.post(URL, json=body, headers=HEADERS)
    assert response.status_code == 202 and response.json()["inserted"] is True
    assert (
        await db_session.scalar(select(func.count()).select_from(IntegrationInbox)) == 1
    )
    assert await db_session.scalar(select(func.count()).select_from(Notification)) == 1
