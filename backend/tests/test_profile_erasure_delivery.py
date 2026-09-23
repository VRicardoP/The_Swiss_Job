"""Deletion requests outlive the account; offline core never loses the intent."""

import uuid
from datetime import datetime, timezone

import pytest

from models.profile_erasure import ProfileErasure
from services import profile_erasure as sync
from services.matching.identity import set_profile_link
from tests.conftest import TestSessionLocal
from tests.test_profile import register_and_get_token


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["profile", "feed"])
@pytest.mark.parametrize("same_profile", [True, False])
async def test_erasure_fences_late_http_cache_publication(kind, same_profile):
    import httpx
    from services.profiles import core_client as pc
    from services.matching import core_client as mc

    pid, other = uuid.uuid4(), uuid.uuid4()
    module = pc if kind == "profile" else mc
    key = str(pid) if kind == "profile" else (str(pid), "")
    other_key = str(other) if kind == "profile" else (str(other), "")
    module._etag_cache[other_key] = ("other", {"preserve": True})

    class Late:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            sync.clear_erased_caches(pid if same_profile else uuid.uuid4())
            return httpx.Response(
                200, headers={"etag": "old"}, json={"content": "synthetic private CV"}
            )

    async def fetch():
        if kind == "profile":
            return await pc.CoreProfile(None, Late)._fetch_profile(pid)
        return await mc.CoreMatching(None)._fetch_page(Late(), pid, None)

    if same_profile:
        with pytest.raises(module.CoreUnavailableError, match="invalidated"):
            await fetch()
        assert key not in module._etag_cache
    else:
        assert await fetch() == {"content": "synthetic private CV"}
        assert key in module._etag_cache
    assert module._etag_cache[other_key][1] == {"preserve": True}
    pc.clear_profile_cache()
    mc.clear_feed_cache()


@pytest.mark.asyncio
async def test_background_without_credentials_keeps_requests_offline(monkeypatch):
    import asyncio
    from config import settings
    from unittest.mock import AsyncMock

    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    drain = AsyncMock()
    monkeypatch.setattr(sync, "drain_erasures", drain)

    async def stop(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(sync.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await sync.run_erasure_delivery()
    drain.assert_not_called()


class Core:
    status_code = 200
    fail = False
    invalid = False
    calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def delete(self, path):
        self.calls += 1
        if self.fail:
            raise ConnectionError("secret must never appear in diagnostics")
        self.pid = path.rsplit("/", 1)[-1]
        return self

    def json(self):
        return {
            "status": "erased",
            "scope": "core_live_database",
            "profile_id": str(uuid.uuid4()) if self.invalid else self.pid,
            "erased_at": datetime.now(timezone.utc).isoformat(),
        }


@pytest.mark.asyncio
async def test_replica_purges_restored_account_even_with_previous_confirmation(client):
    token, _, _ = await register_and_get_token(client)
    uid = uuid.UUID(
        (
            await client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            )
        ).json()["id"]
    )
    pid = uuid.uuid4()
    when = datetime.now(timezone.utc)
    async with TestSessionLocal() as db:
        await sync.queue_erasure(db, uid, pid)
        from sqlalchemy import update

        await db.execute(
            update(ProfileErasure)
            .where(ProfileErasure.user_id == uid)
            .values(core_confirmed_at=when)
        )
        await db.commit()

    class Inventory(Core):
        async def get(self, path, params):
            return self

        def json(self):
            return {
                "consumer": "swissjob-shadow",
                "items": [
                    {
                        "profile_id": str(pid),
                        "external_ref": str(uid),
                        "erased_at": when.isoformat(),
                    }
                ],
                "next_cursor": None,
            }

    assert await sync.reconcile_replica(TestSessionLocal, Inventory) == 1
    assert await sync.reconcile_replica(TestSessionLocal, Inventory) == 0
    assert (
        await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
        )
    ).status_code == 401


async def delete_linked(client):
    token, _, password = await register_and_get_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    uid = uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["id"])
    pid = uuid.uuid4()
    async with TestSessionLocal() as db:
        await set_profile_link(db, uid, pid)
    r = await client.request(
        "DELETE",
        "/api/v1/profile/delete-all",
        headers=headers,
        json={"password": password},
    )
    assert r.status_code == 200, r.text
    assert r.json()["core_erasure"] == "pending_confirmation"
    return uid, pid


@pytest.mark.asyncio
async def test_request_survives_deletion_restart_and_retry(client, monkeypatch):
    uid, pid = await delete_linked(client)
    core = Core()
    monkeypatch.setattr(sync, "default_client_factory", lambda: core)
    core.fail = True
    await sync.drain_erasures(TestSessionLocal)
    async with TestSessionLocal() as db:
        row = await db.get(ProfileErasure, uid)
        assert row.core_profile_id == pid
        assert row.core_confirmed_at is None
        assert row.last_error == "ConnectionError"
    core.fail = False
    await sync.drain_erasures(TestSessionLocal)
    async with TestSessionLocal() as db:
        assert (await db.get(ProfileErasure, uid)).core_confirmed_at is not None
    assert await sync.drain_erasures(TestSessionLocal) == 0
    assert core.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status,invalid", [(404, False), (503, False), (200, True)])
async def test_only_explicit_owned_receipt_confirms(
    client, monkeypatch, status, invalid
):
    uid, _ = await delete_linked(client)
    core = Core()
    core.status_code, core.invalid = status, invalid
    monkeypatch.setattr(sync, "default_client_factory", lambda: core)
    await sync.drain_erasures(TestSessionLocal)
    async with TestSessionLocal() as db:
        assert (await db.get(ProfileErasure, uid)).core_confirmed_at is None
