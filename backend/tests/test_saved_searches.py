"""Tests for saved search CRUD and manual run endpoints."""

import uuid

import pytest
from httpx import AsyncClient

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


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestSavedSearchesCRUD:
    async def test_list_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/searches")
        assert resp.status_code == 401

    async def test_list_empty(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        resp = await client.get("/api/v1/searches", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["data"] == []

    async def test_create_search(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        resp = await client.post(
            "/api/v1/searches",
            headers=_auth(token),
            json={
                "name": "Python Zurich",
                "filters": {"source": "jobs_ch", "canton": "ZH"},
                "min_score": 60,
                "notify_frequency": "daily",
                "notify_push": True,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Python Zurich"
        assert data["filters"] == {"source": "jobs_ch", "canton": "ZH"}
        assert data["min_score"] == 60
        assert data["notify_frequency"] == "daily"
        assert data["is_active"] is True
        assert data["total_matches"] == 0

    async def test_create_defaults(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        resp = await client.post(
            "/api/v1/searches",
            headers=_auth(token),
            json={"name": "Quick Search"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["min_score"] == 0
        assert data["notify_frequency"] == "daily"
        assert data["notify_push"] is True
        assert data["filters"] == {}

    async def test_update_search(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)

        create_resp = await client.post(
            "/api/v1/searches",
            headers=_auth(token),
            json={"name": "Original"},
        )
        search_id = create_resp.json()["id"]

        resp = await client.put(
            f"/api/v1/searches/{search_id}",
            headers=_auth(token),
            json={
                "name": "Updated",
                "notify_frequency": "weekly",
                "is_active": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Updated"
        assert data["notify_frequency"] == "weekly"
        assert data["is_active"] is False

    async def test_update_not_found(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        fake_id = str(uuid.uuid4())
        resp = await client.put(
            f"/api/v1/searches/{fake_id}",
            headers=_auth(token),
            json={"name": "Nope"},
        )
        assert resp.status_code == 404

    async def test_delete_search(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)

        create_resp = await client.post(
            "/api/v1/searches",
            headers=_auth(token),
            json={"name": "To Delete"},
        )
        search_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/searches/{search_id}",
            headers=_auth(token),
        )
        assert resp.status_code == 204

        list_resp = await client.get("/api/v1/searches", headers=_auth(token))
        assert list_resp.json()["total"] == 0

    async def test_delete_not_found(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        fake_id = str(uuid.uuid4())
        resp = await client.delete(
            f"/api/v1/searches/{fake_id}",
            headers=_auth(token),
        )
        assert resp.status_code == 404

    async def test_user_isolation(self, client: AsyncClient):
        token_a, _ = await _register_and_get_token(client)
        token_b, _ = await _register_and_get_token(client)

        await client.post(
            "/api/v1/searches",
            headers=_auth(token_a),
            json={"name": "Secret Search"},
        )

        resp = await client.get("/api/v1/searches", headers=_auth(token_b))
        assert resp.json()["total"] == 0

    async def test_create_validation_empty_name(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        resp = await client.post(
            "/api/v1/searches",
            headers=_auth(token),
            json={"name": ""},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestSavedSearchRun:
    async def test_run_not_found(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        fake_id = str(uuid.uuid4())
        resp = await client.post(
            f"/api/v1/searches/{fake_id}/run",
            headers=_auth(token),
        )
        assert resp.status_code == 404
