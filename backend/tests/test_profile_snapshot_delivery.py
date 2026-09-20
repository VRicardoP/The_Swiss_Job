"""Durable profile delivery: delayed ACKs, outages, restore mismatch and opt-in."""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from config import settings
from services import profile_sync as sync
from tests.conftest import TestSessionLocal
from tests.test_analytics_router import _auth


class Core:
    def __init__(self):
        self.version = 0
        self.calls = []
        self.body = None
        self.fail = False
        self.on_put = None
        self.ack_override = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def put(self, url, json):
        self.calls.append(json)
        if self.fail:
            raise ConnectionError("PRIVATE CV MUST NOT ENTER DIAGNOSTICS")
        if self.on_put:
            callback, self.on_put = self.on_put, None
            await callback()
        if json["version"] > self.version:
            self.version, self.body = json["version"], json
        ack = self.version if self.ack_override is None else self.ack_override

        class Response:
            status_code = 200

            def json(self):
                return {"version": ack}

        return Response()


@pytest.fixture
def core(monkeypatch):
    remote = Core()
    monkeypatch.setattr(settings, "CORE_PROFILE_SYNC_ENABLED", True)
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "synthetic-test-key")
    monkeypatch.setattr(sync, "resolve_mode", AsyncMock(return_value="core_primary"))
    monkeypatch.setattr(
        sync, "resolve_core_profile_id", AsyncMock(return_value=uuid.uuid4())
    )
    monkeypatch.setattr(sync, "default_client_factory", lambda: remote)
    return remote


async def _deliver(uid):
    async with TestSessionLocal() as db:
        return await sync.sync_profile_snapshot(db, uid)


async def test_no_delivery_without_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "CORE_PROFILE_SYNC_ENABLED", False)
    db = AsyncMock()
    assert await sync.sync_profile_snapshot(db, uuid.uuid4()) == {"status": "disabled"}
    db.execute.assert_not_awaited()


async def test_outage_does_not_break_edit_and_retry_is_durable(client, core):
    headers, uid = await _auth(client)
    core.fail = True
    edited = await client.put(
        "/api/v1/profile", headers=headers, json={"title": "Fresh CV"}
    )
    assert edited.status_code == 200
    assert (await _deliver(uid))["status"] == "pending"
    status = (await client.get("/api/v1/profile/sync-status", headers=headers)).json()
    assert status["pending"] and status["last_error"] == "ConnectionError"
    assert "PRIVATE" not in str(status)
    core.fail = False
    assert await sync.drain_profile_snapshots(TestSessionLocal) == 1
    assert core.body["content"]["title"] == "Fresh CV"
    assert not (
        await client.get("/api/v1/profile/sync-status", headers=headers)
    ).json()["pending"]
    assert await sync.drain_profile_snapshots(TestSessionLocal) == 0


async def test_old_ack_cannot_clear_a_newer_pending_edit(client, core):
    headers, uid = await _auth(client)

    async def edit_during_http():
        # A separate transaction can commit while HTTP runs: no source locks held.
        async with TestSessionLocal() as db:
            await db.execute(text("SET LOCAL lock_timeout='1s'"))
            await db.execute(
                text("UPDATE user_profiles SET title='Newer' WHERE user_id=:u"),
                {"u": uid},
            )
            await db.commit()

    core.on_put = edit_during_http
    assert (await _deliver(uid))["status"] == "ok"
    assert (await client.get("/api/v1/profile/sync-status", headers=headers)).json()[
        "pending"
    ]
    assert (await _deliver(uid))["status"] == "ok"
    assert core.body["content"]["title"] == "Newer"
    assert not (
        await client.get("/api/v1/profile/sync-status", headers=headers)
    ).json()["pending"]


@pytest.mark.parametrize(
    "ack,error",
    [(True, "invalid_ack"), (0, "invalid_ack"), (999, "core_version_ahead")],
)
async def test_invalid_or_ahead_ack_never_silently_marks_delivered(
    client, core, ack, error
):
    headers, uid = await _auth(client)
    core.ack_override = ack
    result = await _deliver(uid)
    assert result == {"status": "pending", "error": error}
    assert (await client.get("/api/v1/profile/sync-status", headers=headers)).json()[
        "pending"
    ]


async def test_missing_credentials_or_enrollment_send_nothing(
    client, core, monkeypatch
):
    _, uid = await _auth(client)
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    assert (await _deliver(uid))["error"] == "not_configured"
    monkeypatch.setattr(sync, "resolve_core_profile_id", AsyncMock(return_value=None))
    assert (await _deliver(uid))["error"] == "not_enrolled"
    assert core.calls == []


async def test_status_requires_authentication(client):
    assert (await client.get("/api/v1/profile/sync-status")).status_code == 401
