"""Tests for notification history and mark-read endpoints."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.notification import Notification
from tests.conftest import random_email


_TEST_PASSWORD = "TestPass123!"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_get_token(client: AsyncClient) -> tuple[str, str]:
    email = random_email()
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _TEST_PASSWORD, "gdpr_consent": True},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"], email


async def _get_user_id(client: AsyncClient, token: str) -> uuid.UUID:
    resp = await client.get("/api/v1/auth/me", headers=_auth(token))
    return uuid.UUID(resp.json()["id"])


async def _insert_notification(
    db: AsyncSession, user_id: uuid.UUID, **overrides
) -> Notification:
    defaults = {
        "user_id": user_id,
        "event_type": "new_matches",
        "title": "New matches found",
        "body": "3 new jobs match your search.",
        "data": {"search_id": str(uuid.uuid4()), "match_count": 3},
        "is_read": False,
    }
    defaults.update(overrides)
    n = Notification(**defaults)
    db.add(n)
    await db.commit()
    await db.refresh(n)
    return n


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestNotificationHistory:
    async def test_list_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/notifications")
        assert resp.status_code == 401

    async def test_list_empty(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        resp = await client.get("/api/v1/notifications", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["unread_count"] == 0
        assert data["data"] == []

    async def test_list_with_data(self, client: AsyncClient, db_session: AsyncSession):
        token, _ = await _register_and_get_token(client)
        user_id = await _get_user_id(client, token)

        await _insert_notification(db_session, user_id)
        await _insert_notification(db_session, user_id, is_read=True, title="Old one")

        resp = await client.get("/api/v1/notifications", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["unread_count"] == 1

    async def test_list_pagination(self, client: AsyncClient, db_session: AsyncSession):
        token, _ = await _register_and_get_token(client)
        user_id = await _get_user_id(client, token)

        for i in range(5):
            await _insert_notification(db_session, user_id, title=f"Notif {i}")

        resp = await client.get(
            "/api/v1/notifications",
            headers=_auth(token),
            params={"limit": 2, "offset": 0},
        )
        data = resp.json()
        assert data["total"] == 5
        assert len(data["data"]) == 2

    async def test_user_isolation(self, client: AsyncClient, db_session: AsyncSession):
        token_a, _ = await _register_and_get_token(client)
        token_b, _ = await _register_and_get_token(client)
        user_a = await _get_user_id(client, token_a)

        await _insert_notification(db_session, user_a)

        resp = await client.get("/api/v1/notifications", headers=_auth(token_b))
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Mark read
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestNotificationMarkRead:
    async def test_mark_read(self, client: AsyncClient, db_session: AsyncSession):
        token, _ = await _register_and_get_token(client)
        user_id = await _get_user_id(client, token)

        n = await _insert_notification(db_session, user_id)

        resp = await client.put(
            f"/api/v1/notifications/{n.id}/read",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["is_read"] is True

        # Verify unread count updated
        list_resp = await client.get("/api/v1/notifications", headers=_auth(token))
        assert list_resp.json()["unread_count"] == 0

    async def test_mark_read_not_found(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        fake_id = str(uuid.uuid4())
        resp = await client.put(
            f"/api/v1/notifications/{fake_id}/read",
            headers=_auth(token),
        )
        assert resp.status_code == 404

    async def test_mark_read_other_user(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Users cannot mark other users' notifications as read."""
        token_a, _ = await _register_and_get_token(client)
        token_b, _ = await _register_and_get_token(client)
        user_a = await _get_user_id(client, token_a)

        n = await _insert_notification(db_session, user_a)

        resp = await client.put(
            f"/api/v1/notifications/{n.id}/read",
            headers=_auth(token_b),
        )
        assert resp.status_code == 404
