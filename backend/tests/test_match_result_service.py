"""Tests de caracterización para los métodos de lectura/CRUD de resultados.

Cubren clear_feedback y get_saved_jobs (sin tests hasta ahora) antes de
extraerlos de MatchService a MatchResultService. La indirección `_svc` permite
apuntar al nuevo servicio tras la extracción sin reescribir los tests.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.match_result import MatchResult
from services.match_result_service import MatchResultService
from tests.conftest import random_email

_PW = "TestPass123!"


def _svc(db: AsyncSession):
    """Servicio con los métodos de lectura/CRUD de resultados."""
    return MatchResultService(db)


async def _register(client: AsyncClient) -> uuid.UUID:
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": random_email(), "password": _PW, "gdpr_consent": True},
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    return uuid.UUID(me.json()["id"])


async def _insert_job(db: AsyncSession, h: str) -> None:
    valid = {c.key for c in Job.__table__.columns}
    data = {
        "hash": h,
        "source": "test",
        "title": f"Job {h}",
        "company": "C",
        "url": f"https://x.ch/{h}",
        "is_active": True,
    }
    db.add(Job(**{k: v for k, v in data.items() if k in valid}))
    await db.commit()


async def _seed(
    db: AsyncSession, user_id: uuid.UUID, h: str, score: float = 10.0, **over
) -> None:
    mr = MatchResult(
        user_id=user_id,
        job_hash=h,
        score_embedding=0.1,
        score_salary=0.1,
        score_location=0.1,
        score_recency=0.1,
        score_llm=0.0,
        score_final=score,
        matching_skills=[],
        missing_skills=[],
    )
    for k, v in over.items():
        setattr(mr, k, v)
    db.add(mr)
    await db.commit()


@pytest.mark.anyio
class TestClearFeedback:
    async def test_clears_existing(self, client: AsyncClient, db_session: AsyncSession):
        user_id = await _register(client)
        await _insert_job(db_session, "cf1")
        await _seed(db_session, user_id, "cf1", feedback="thumbs_down")

        match = await _svc(db_session).clear_feedback(user_id, "cf1")
        assert match is not None
        assert match.feedback is None

    async def test_returns_none_when_missing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user_id = await _register(client)
        assert await _svc(db_session).clear_feedback(user_id, "nope") is None


@pytest.mark.anyio
class TestGetSavedJobs:
    async def test_returns_only_positive_feedback_sorted(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user_id = await _register(client)
        for h in ("s1", "s2", "s3", "s4"):
            await _insert_job(db_session, h)
        await _seed(db_session, user_id, "s1", score=30.0, feedback="thumbs_up")
        await _seed(db_session, user_id, "s2", score=80.0, feedback="applied")
        await _seed(db_session, user_id, "s3", feedback="thumbs_down")  # excluido
        await _seed(db_session, user_id, "s4")  # sin feedback → excluido

        results, total = await _svc(db_session).get_saved_jobs(user_id)

        hashes = [r["match"].job_hash for r in results]
        assert total == 2
        assert hashes == ["s2", "s1"]  # ordenado por score_final desc

    async def test_pagination(self, client: AsyncClient, db_session: AsyncSession):
        user_id = await _register(client)
        for i in range(3):
            await _insert_job(db_session, f"p{i}")
            await _seed(
                db_session, user_id, f"p{i}", score=float(i), feedback="applied"
            )

        results, total = await _svc(db_session).get_saved_jobs(
            user_id, limit=2, offset=0
        )
        assert total == 3
        assert len(results) == 2  # limit aplicado
