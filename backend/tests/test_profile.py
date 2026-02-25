"""Tests for GDPR profile endpoints: export and delete-all."""

from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from tests.conftest import random_email


async def register_and_get_token(client: AsyncClient) -> tuple[str, str]:
    """Helper: register a user and return (access_token, email)."""
    email = random_email()
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "TestPass123!", "gdpr_consent": True},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"], email


class TestProfileExport:
    async def test_export_returns_user_data(self, client: AsyncClient):
        token, email = await register_and_get_token(client)
        resp = await client.get(
            "/api/v1/profile/export",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == email
        assert data["gdpr_consent"] is True
        assert "exported_at" in data
        assert "profile" in data
        assert data["profile"] is not None
        assert data["profile"]["skills"] == []

    async def test_export_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/profile/export")
        assert resp.status_code == 401

    async def test_export_with_invalid_token(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/profile/export",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401


class TestProfileDeleteAll:
    async def test_delete_removes_user(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, email = await register_and_get_token(client)
        resp = await client.delete(
            "/api/v1/profile/delete-all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "All user data has been permanently deleted"
        assert "deleted_at" in data

        # Verify user is gone from DB
        result = await db_session.execute(
            select(User).where(User.email == email)
        )
        assert result.scalar_one_or_none() is None

    async def test_delete_cascades_profile(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _email = await register_and_get_token(client)

        # Get user_id before deletion
        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        user_id = me_resp.json()["id"]

        resp = await client.delete(
            "/api/v1/profile/delete-all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Verify profile is also gone
        result = await db_session.execute(
            text("SELECT id FROM user_profiles WHERE user_id = :uid"),
            {"uid": user_id},
        )
        assert result.fetchone() is None

    async def test_delete_requires_auth(self, client: AsyncClient):
        resp = await client.delete("/api/v1/profile/delete-all")
        assert resp.status_code == 401

    async def test_token_invalid_after_delete(self, client: AsyncClient):
        token, _email = await register_and_get_token(client)

        # Delete account
        resp = await client.delete(
            "/api/v1/profile/delete-all",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200

        # Old token should no longer work
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
