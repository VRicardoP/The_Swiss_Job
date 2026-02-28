"""Tests for job application CRUD and stats endpoints."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
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


def _job_data(idx: int, **overrides) -> dict:
    base = {
        "hash": f"app{idx:029d}"[:32],
        "source": "test_source",
        "title": f"Engineer {idx}",
        "company": f"Corp {idx}",
        "url": f"https://example.com/job/{idx}",
        "location": "Zurich, ZH",
        "canton": "ZH",
        "remote": False,
        "tags": ["python"],
        "is_active": True,
    }
    base.update(overrides)
    return base


async def _insert_job(db: AsyncSession, idx: int = 0, **overrides) -> str:
    valid_columns = {c.key for c in Job.__table__.columns}
    data = _job_data(idx, **overrides)
    job = Job(**{k: v for k, v in data.items() if k in valid_columns})
    db.add(job)
    await db.commit()
    return data["hash"]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestApplicationsCRUD:
    async def test_list_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/applications")
        assert resp.status_code == 401

    async def test_list_empty(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        resp = await client.get("/api/v1/applications", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["data"] == []

    async def test_create_application(self, client: AsyncClient, db_session: AsyncSession):
        token, _ = await _register_and_get_token(client)
        job_hash = await _insert_job(db_session)

        resp = await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": job_hash, "notes": "Looks great"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["job_hash"] == job_hash
        assert data["status"] == "saved"
        assert data["notes"] == "Looks great"
        assert data["job_title"] == "Engineer 0"
        assert data["job_company"] == "Corp 0"

    async def test_create_job_not_found(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        resp = await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": "nonexistent000000000000000000000"},
        )
        assert resp.status_code == 404

    async def test_create_duplicate_conflict(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _ = await _register_and_get_token(client)
        job_hash = await _insert_job(db_session)

        resp1 = await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": job_hash},
        )
        assert resp1.status_code == 201

        resp2 = await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": job_hash},
        )
        assert resp2.status_code == 409

    async def test_update_status(self, client: AsyncClient, db_session: AsyncSession):
        token, _ = await _register_and_get_token(client)
        job_hash = await _insert_job(db_session)

        create_resp = await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": job_hash},
        )
        app_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/applications/{app_id}",
            headers=_auth(token),
            json={"status": "applied"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "applied"
        assert data["applied_at"] is not None  # auto-transition

    async def test_update_notes(self, client: AsyncClient, db_session: AsyncSession):
        token, _ = await _register_and_get_token(client)
        job_hash = await _insert_job(db_session)

        create_resp = await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": job_hash},
        )
        app_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/api/v1/applications/{app_id}",
            headers=_auth(token),
            json={"notes": "Updated notes"},
        )
        assert resp.status_code == 200
        assert resp.json()["notes"] == "Updated notes"

    async def test_update_not_found(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        fake_id = str(uuid.uuid4())
        resp = await client.patch(
            f"/api/v1/applications/{fake_id}",
            headers=_auth(token),
            json={"status": "applied"},
        )
        assert resp.status_code == 404

    async def test_delete_application(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _ = await _register_and_get_token(client)
        job_hash = await _insert_job(db_session)

        create_resp = await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": job_hash},
        )
        app_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/api/v1/applications/{app_id}",
            headers=_auth(token),
        )
        assert resp.status_code == 204

        # Verify deleted
        list_resp = await client.get("/api/v1/applications", headers=_auth(token))
        assert list_resp.json()["total"] == 0

    async def test_delete_not_found(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        fake_id = str(uuid.uuid4())
        resp = await client.delete(
            f"/api/v1/applications/{fake_id}",
            headers=_auth(token),
        )
        assert resp.status_code == 404

    async def test_list_filter_by_status(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _ = await _register_and_get_token(client)
        h1 = await _insert_job(db_session, idx=1)
        h2 = await _insert_job(db_session, idx=2)

        # Create two apps
        r1 = await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": h1},
        )
        await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": h2},
        )

        # Move one to applied
        await client.patch(
            f"/api/v1/applications/{r1.json()['id']}",
            headers=_auth(token),
            json={"status": "applied"},
        )

        # Filter by saved
        resp = await client.get(
            "/api/v1/applications",
            headers=_auth(token),
            params={"status": "saved"},
        )
        data = resp.json()
        assert data["total"] == 1
        assert data["data"][0]["status"] == "saved"

    async def test_auto_transition_applied_at_not_overwritten(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """applied_at should only be set on first transition to applied."""
        token, _ = await _register_and_get_token(client)
        job_hash = await _insert_job(db_session)

        create_resp = await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": job_hash},
        )
        app_id = create_resp.json()["id"]

        # First transition to applied
        resp1 = await client.patch(
            f"/api/v1/applications/{app_id}",
            headers=_auth(token),
            json={"status": "applied"},
        )
        first_applied_at = resp1.json()["applied_at"]

        # Move to interview, then back to applied
        await client.patch(
            f"/api/v1/applications/{app_id}",
            headers=_auth(token),
            json={"status": "interview"},
        )
        resp2 = await client.patch(
            f"/api/v1/applications/{app_id}",
            headers=_auth(token),
            json={"status": "applied"},
        )
        # applied_at should not change (already set)
        assert resp2.json()["applied_at"] == first_applied_at

    async def test_user_isolation(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Users cannot see each other's applications."""
        token_a, _ = await _register_and_get_token(client)
        token_b, _ = await _register_and_get_token(client)
        job_hash = await _insert_job(db_session)

        await client.post(
            "/api/v1/applications",
            headers=_auth(token_a),
            json={"job_hash": job_hash},
        )

        resp = await client.get("/api/v1/applications", headers=_auth(token_b))
        assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestApplicationStats:
    async def test_stats_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/applications/stats")
        assert resp.status_code == 401

    async def test_stats_empty(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        resp = await client.get("/api/v1/applications/stats", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["by_status"] == {}
        assert data["conversion_rates"] == {}

    async def test_stats_with_data(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _ = await _register_and_get_token(client)
        h1 = await _insert_job(db_session, idx=10)
        h2 = await _insert_job(db_session, idx=11)

        # Create and transition apps
        r1 = await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": h1},
        )
        await client.post(
            "/api/v1/applications",
            headers=_auth(token),
            json={"job_hash": h2},
        )

        await client.patch(
            f"/api/v1/applications/{r1.json()['id']}",
            headers=_auth(token),
            json={"status": "applied"},
        )

        resp = await client.get("/api/v1/applications/stats", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert "applied" in data["by_status"]
        assert "saved" in data["by_status"]
        assert "by_source" in data
