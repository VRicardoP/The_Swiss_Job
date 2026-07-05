"""Tests de caracterización para el router de analytics (routers/analytics.py).

Cubre filtros de exclusión (crear/listar/borrar), generación de sugerencias a
partir de jobs rechazados, y revisión (approve/reject) con creación de JobFilter.

test_approved_suggestion_listable_as_approved verifica el mapeo verbo→participio
del status (approve→approved) que permite listar las sugerencias ya revisadas.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.match_result import MatchResult
from tests.conftest import random_email

_PW = "TestPass123!"


async def _auth(client: AsyncClient) -> tuple[dict, uuid.UUID]:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": random_email(), "password": _PW, "gdpr_consent": True},
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    return headers, uuid.UUID(me.json()["id"])


async def _seed_rejected(db: AsyncSession, user_id: uuid.UUID, n: int) -> None:
    """Inserta n jobs 'Primary Teacher' rechazados para el usuario."""
    for i in range(n):
        h = f"an{i}"
        valid = {c.key for c in Job.__table__.columns}
        data = {
            "hash": h,
            "source": "test",
            "title": f"Primary Teacher {i}",
            "company": "C",
            "url": f"https://x.ch/{h}",
            "is_active": True,
            "tags": [],
        }
        db.add(Job(**{k: v for k, v in data.items() if k in valid}))
        await db.commit()
        db.add(
            MatchResult(
                user_id=user_id,
                job_hash=h,
                score_embedding=0.1,
                score_salary=0.1,
                score_location=0.1,
                score_recency=0.1,
                score_llm=0.0,
                score_final=10.0,
                matching_skills=[],
                missing_skills=[],
                feedback="dismissed",
            )
        )
        await db.commit()


@pytest.mark.anyio
class TestFilters:
    async def test_create_lists_and_delete(self, client: AsyncClient):
        headers, _ = await _auth(client)

        resp = await client.post(
            "/api/v1/analytics/filters",
            headers=headers,
            json={"filter_type": "title_contains", "pattern": "  TEACHER  "},
        )
        assert resp.status_code == 201
        filter_id = resp.json()["id"]
        assert resp.json()["pattern"] == "teacher"  # lower + strip

        listed = await client.get("/api/v1/analytics/filters", headers=headers)
        assert listed.json()["total"] == 1

        deleted = await client.delete(
            f"/api/v1/analytics/filters/{filter_id}", headers=headers
        )
        assert deleted.status_code == 204

        after = await client.get("/api/v1/analytics/filters", headers=headers)
        assert after.json()["total"] == 0  # desactivado → ya no aparece

    async def test_delete_missing_returns_404(self, client: AsyncClient):
        headers, _ = await _auth(client)
        resp = await client.delete(
            f"/api/v1/analytics/filters/{uuid.uuid4()}", headers=headers
        )
        assert resp.status_code == 404


@pytest.mark.anyio
class TestSuggestions:
    async def test_invalid_status_filter_returns_422(self, client: AsyncClient):
        headers, _ = await _auth(client)
        resp = await client.get(
            "/api/v1/analytics/suggestions?status_filter=bogus", headers=headers
        )
        assert resp.status_code == 422

    async def test_analyze_generates_and_lists_pending(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers, user_id = await _auth(client)
        await _seed_rejected(db_session, user_id, 3)

        resp = await client.post(
            "/api/v1/analytics/analyze", headers=headers, json={"min_rejected": 2}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["rejected_jobs_analyzed"] == 3
        assert body["suggestions_generated"] >= 1

        pending = await client.get("/api/v1/analytics/suggestions", headers=headers)
        assert pending.json()["total"] >= 1

    async def test_review_approve_creates_filter(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        headers, user_id = await _auth(client)
        await _seed_rejected(db_session, user_id, 3)
        await client.post(
            "/api/v1/analytics/analyze", headers=headers, json={"min_rejected": 2}
        )
        sug_id = (
            await client.get("/api/v1/analytics/suggestions", headers=headers)
        ).json()["data"][0]["id"]

        review = await client.post(
            f"/api/v1/analytics/suggestions/{sug_id}/review",
            headers=headers,
            json={"action": "approve"},
        )
        assert review.status_code == 200
        assert review.json()["filter_id"] is not None  # se creó un JobFilter

        # Re-revisar la misma sugerencia → 409 (ya no está pending)
        again = await client.post(
            f"/api/v1/analytics/suggestions/{sug_id}/review",
            headers=headers,
            json={"action": "approve"},
        )
        assert again.status_code == 409

    async def test_review_missing_returns_404(self, client: AsyncClient):
        headers, _ = await _auth(client)
        resp = await client.post(
            f"/api/v1/analytics/suggestions/{uuid.uuid4()}/review",
            headers=headers,
            json={"action": "reject"},
        )
        assert resp.status_code == 404

    async def test_approved_suggestion_listable_as_approved(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Al aprobar (action='approve'), el status persistido es 'approved'
        (participio), de modo que ?status_filter=approved SÍ devuelve la
        sugerencia revisada y ya NO aparece entre las pendientes."""
        headers, user_id = await _auth(client)
        await _seed_rejected(db_session, user_id, 3)
        await client.post(
            "/api/v1/analytics/analyze", headers=headers, json={"min_rejected": 2}
        )
        sug_id = (
            await client.get("/api/v1/analytics/suggestions", headers=headers)
        ).json()["data"][0]["id"]
        await client.post(
            f"/api/v1/analytics/suggestions/{sug_id}/review",
            headers=headers,
            json={"action": "approve"},
        )

        approved = await client.get(
            "/api/v1/analytics/suggestions?status_filter=approved", headers=headers
        )
        assert any(s["id"] == sug_id for s in approved.json()["data"])

        pending = await client.get(
            "/api/v1/analytics/suggestions?status_filter=pending", headers=headers
        )
        assert all(s["id"] != sug_id for s in pending.json()["data"])
