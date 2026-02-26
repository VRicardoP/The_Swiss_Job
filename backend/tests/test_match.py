"""Tests for AI match endpoints — analyze, results, history, feedback, implicit."""

import uuid

import numpy as np
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.user_profile import UserProfile
from tests.conftest import random_email


_TEST_PASSWORD = "TestPass123!"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_get_token(client: AsyncClient) -> tuple[str, str]:
    """Register a user and return (access_token, email)."""
    email = random_email()
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _TEST_PASSWORD, "gdpr_consent": True},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"], email


def _fake_embedding(seed: int = 42) -> list[float]:
    """Generate a deterministic 384-dim normalized embedding."""
    rng = np.random.RandomState(seed)
    vec = rng.randn(384).astype(np.float32)
    vec = vec / np.linalg.norm(vec)
    return vec.tolist()


def _job_data(idx: int, **overrides) -> dict:
    """Factory for Job constructor kwargs with unique hash/url."""
    base = {
        "hash": f"mt{idx:030d}"[:32],
        "source": "test_source",
        "title": f"Software Engineer {idx}",
        "company": f"Company {idx}",
        "url": f"https://example.com/job/{idx}",
        "location": "Zurich, ZH",
        "canton": "ZH",
        "description": "Build distributed systems with Python and PostgreSQL",
        "description_snippet": "Build distributed systems...",
        "remote": False,
        "tags": ["python", "postgresql", "fastapi"],
        "language": "en",
        "seniority": "mid",
        "contract_type": "full_time",
        "salary_min_chf": 80000 + idx * 1000,
        "salary_max_chf": 120000 + idx * 1000,
        "is_active": True,
    }
    base.update(overrides)
    return base


async def _insert_jobs_with_embeddings(db: AsyncSession, count: int = 5) -> list[str]:
    """Insert N jobs with embeddings. Returns list of hashes."""
    valid_columns = {c.key for c in Job.__table__.columns}
    hashes = []
    for i in range(count):
        data = _job_data(i)
        job = Job(**{k: v for k, v in data.items() if k in valid_columns})
        job.embedding = _fake_embedding(seed=100 + i)
        db.add(job)
        hashes.append(data["hash"])
    await db.commit()
    return hashes


async def _setup_user_with_embedding(
    client: AsyncClient, db: AsyncSession
) -> tuple[str, uuid.UUID]:
    """Register user, set profile embedding. Returns (token, user_id)."""
    token, email = await _register_and_get_token(client)

    # Update profile with skills and locations
    await client.put(
        "/api/v1/profile",
        headers=_auth(token),
        json={
            "skills": ["python", "fastapi", "postgresql"],
            "locations": ["Zurich"],
            "salary_min": 80000,
            "salary_max": 120000,
        },
    )

    # Directly set cv_embedding on the profile
    from sqlalchemy import select

    me_resp = await client.get("/api/v1/auth/me", headers=_auth(token))
    user_id = uuid.UUID(me_resp.json()["id"])

    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = result.scalar_one()
    profile.cv_embedding = _fake_embedding(seed=1)
    await db.commit()

    return token, user_id


# ---------------------------------------------------------------------------
# Analyze endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestMatchAnalyze:
    async def test_analyze_requires_auth(self, client: AsyncClient):
        resp = await client.post("/api/v1/match/analyze")
        assert resp.status_code == 401

    async def test_analyze_no_embedding_returns_400(self, client: AsyncClient):
        token, _email = await _register_and_get_token(client)
        resp = await client.post("/api/v1/match/analyze", headers=_auth(token))
        assert resp.status_code == 400
        assert "CV embedding" in resp.json()["detail"]

    async def test_analyze_no_jobs_returns_success(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        resp = await client.post("/api/v1/match/analyze", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "no_jobs"
        assert data["results_count"] == 0

    async def test_analyze_success(self, client: AsyncClient, db_session: AsyncSession):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        await _insert_jobs_with_embeddings(db_session, count=5)

        resp = await client.post("/api/v1/match/analyze", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["total_candidates"] == 5
        assert data["results_count"] == 5

    async def test_analyze_respects_top_k(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        await _insert_jobs_with_embeddings(db_session, count=10)

        resp = await client.post(
            "/api/v1/match/analyze",
            headers=_auth(token),
            json={"top_k": 3},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["results_count"] == 3
        assert data["total_candidates"] == 10


# ---------------------------------------------------------------------------
# Results endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestMatchResults:
    async def test_results_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/match/results")
        assert resp.status_code == 401

    async def test_results_empty_before_analyze(self, client: AsyncClient):
        token, _email = await _register_and_get_token(client)
        resp = await client.get("/api/v1/match/results", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["data"] == []

    async def test_results_after_analyze(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        await _insert_jobs_with_embeddings(db_session, count=5)

        # Run analysis first
        await client.post("/api/v1/match/analyze", headers=_auth(token))

        resp = await client.get("/api/v1/match/results", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["data"]) == 5
        assert "weights_used" in data

        # Verify results are sorted descending by score
        scores = [r["score_final"] for r in data["data"]]
        assert scores == sorted(scores, reverse=True)

    async def test_results_include_job_details(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        await _insert_jobs_with_embeddings(db_session, count=1)

        await client.post("/api/v1/match/analyze", headers=_auth(token))

        resp = await client.get("/api/v1/match/results", headers=_auth(token))
        result = resp.json()["data"][0]
        assert result["job_title"] is not None
        assert result["job_company"] is not None
        assert result["job_url"] is not None
        assert "scores" in result
        assert "embedding" in result["scores"]

    async def test_results_pagination(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        await _insert_jobs_with_embeddings(db_session, count=5)

        await client.post("/api/v1/match/analyze", headers=_auth(token))

        resp = await client.get(
            "/api/v1/match/results",
            headers=_auth(token),
            params={"limit": 2, "offset": 0},
        )
        data = resp.json()
        assert data["total"] == 5
        assert len(data["data"]) == 2

        resp2 = await client.get(
            "/api/v1/match/results",
            headers=_auth(token),
            params={"limit": 2, "offset": 2},
        )
        data2 = resp2.json()
        assert len(data2["data"]) == 2

        # No overlap between pages
        hashes_page1 = {r["job_hash"] for r in data["data"]}
        hashes_page2 = {r["job_hash"] for r in data2["data"]}
        assert hashes_page1.isdisjoint(hashes_page2)


# ---------------------------------------------------------------------------
# Feedback endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestMatchFeedback:
    async def test_feedback_requires_auth(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/match/fakehash/feedback",
            json={"feedback": "thumbs_up"},
        )
        assert resp.status_code == 401

    async def test_feedback_not_found(self, client: AsyncClient):
        token, _email = await _register_and_get_token(client)
        resp = await client.post(
            "/api/v1/match/nonexistent00000000000000000000/feedback",
            headers=_auth(token),
            json={"feedback": "thumbs_up"},
        )
        assert resp.status_code == 404

    async def test_feedback_thumbs_up(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        hashes = await _insert_jobs_with_embeddings(db_session, count=1)

        await client.post("/api/v1/match/analyze", headers=_auth(token))

        resp = await client.post(
            f"/api/v1/match/{hashes[0]}/feedback",
            headers=_auth(token),
            json={"feedback": "thumbs_up"},
        )
        assert resp.status_code == 200
        assert resp.json()["feedback"] == "thumbs_up"

        # Verify feedback persisted in results
        results_resp = await client.get("/api/v1/match/results", headers=_auth(token))
        assert results_resp.json()["data"][0]["feedback"] == "thumbs_up"

    async def test_feedback_invalid_value(self, client: AsyncClient):
        token, _email = await _register_and_get_token(client)
        resp = await client.post(
            "/api/v1/match/somehash0000000000000000000000/feedback",
            headers=_auth(token),
            json={"feedback": "invalid_value"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# History endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestMatchHistory:
    async def test_history_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/match/history")
        assert resp.status_code == 401

    async def test_history_empty(self, client: AsyncClient):
        token, _email = await _register_and_get_token(client)
        resp = await client.get("/api/v1/match/history", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["data"] == []

    async def test_history_returns_results(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        await _insert_jobs_with_embeddings(db_session, count=3)

        await client.post("/api/v1/match/analyze", headers=_auth(token))

        resp = await client.get("/api/v1/match/history", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["data"]) == 3
        assert "weights_used" in data

    async def test_history_pagination(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        await _insert_jobs_with_embeddings(db_session, count=5)

        await client.post("/api/v1/match/analyze", headers=_auth(token))

        resp = await client.get(
            "/api/v1/match/history",
            headers=_auth(token),
            params={"limit": 2, "offset": 0},
        )
        data = resp.json()
        assert data["total"] == 5
        assert len(data["data"]) == 2


# ---------------------------------------------------------------------------
# Implicit feedback endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestImplicitFeedback:
    async def test_implicit_requires_auth(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/match/fakehash/implicit",
            json={"action": "opened"},
        )
        assert resp.status_code == 401

    async def test_implicit_not_found(self, client: AsyncClient):
        token, _email = await _register_and_get_token(client)
        resp = await client.post(
            "/api/v1/match/nonexistent00000000000000000000/implicit",
            headers=_auth(token),
            json={"action": "opened"},
        )
        assert resp.status_code == 404

    async def test_implicit_opened(self, client: AsyncClient, db_session: AsyncSession):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        hashes = await _insert_jobs_with_embeddings(db_session, count=1)

        await client.post("/api/v1/match/analyze", headers=_auth(token))

        resp = await client.post(
            f"/api/v1/match/{hashes[0]}/implicit",
            headers=_auth(token),
            json={"action": "opened"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["action"] == "opened"

    async def test_implicit_view_time_with_duration(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        hashes = await _insert_jobs_with_embeddings(db_session, count=1)

        await client.post("/api/v1/match/analyze", headers=_auth(token))

        resp = await client.post(
            f"/api/v1/match/{hashes[0]}/implicit",
            headers=_auth(token),
            json={"action": "view_time", "duration_ms": 15000},
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "view_time"

    async def test_implicit_multiple_signals_accumulate(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _uid = await _setup_user_with_embedding(client, db_session)
        hashes = await _insert_jobs_with_embeddings(db_session, count=1)

        await client.post("/api/v1/match/analyze", headers=_auth(token))

        # Send multiple signals
        for action in ["opened", "view_time", "saved"]:
            resp = await client.post(
                f"/api/v1/match/{hashes[0]}/implicit",
                headers=_auth(token),
                json={"action": action},
            )
            assert resp.status_code == 200

    async def test_implicit_invalid_action(self, client: AsyncClient):
        token, _email = await _register_and_get_token(client)
        resp = await client.post(
            "/api/v1/match/somehash0000000000000000000000/implicit",
            headers=_auth(token),
            json={"action": "invalid_action"},
        )
        assert resp.status_code == 422
