"""Tests de caracterización para los métodos de lectura/CRUD de resultados.

Cubren clear_feedback y get_saved_jobs (sin tests hasta ahora) antes de
extraerlos de MatchService a MatchResultService. La indirección `_svc` permite
apuntar al nuevo servicio tras la extracción sin reescribir los tests.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import update
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
class TestGetResultsHidesArchivedJobs:
    """PF.3: una oferta archivada (is_active=False) no debe reaparecer en el feed."""

    async def test_archived_job_not_shown(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user_id = await _register(client)
        await _insert_job(db_session, "arch1")
        await _seed(db_session, user_id, "arch1")  # feedback=None -> visible

        _, total = await _svc(db_session).get_results(user_id)
        assert total == 1  # visible mientras está activa

        # Archivar (como hace cleanup_stale_jobs con ofertas caducadas + adjuntos)
        await db_session.execute(
            update(Job).where(Job.hash == "arch1").values(is_active=False)
        )
        await db_session.commit()

        results, total = await _svc(db_session).get_results(user_id)
        assert total == 0
        assert results == []

    async def test_reactivated_duplicate_not_shown(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Un duplicado reactivado (is_active=True pero duplicate_of set) NO debe
        aparecer en el feed."""
        user_id = await _register(client)
        await _insert_job(db_session, "dup1")
        await _seed(db_session, user_id, "dup1")
        await db_session.execute(
            update(Job)
            .where(Job.hash == "dup1")
            .values(duplicate_of="canonical000000", is_active=True)
        )
        await db_session.commit()

        _, total = await _svc(db_session).get_results(user_id)
        assert total == 0  # excluido por duplicate_of IS NOT NULL


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
class TestRecordImplicitFeedback:
    """Simetria implicit/explicit (2ª rev. A.SEAM matching): el huerfano
    legacy con Job local comparte el camino de upsert minimo del explicito."""

    async def test_orphan_with_local_job_upserts_minimal_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user_id = await _register(client)
        await _insert_job(db_session, "imp1")  # Job local, SIN fila MatchResult

        match = await _svc(db_session).record_implicit_feedback(
            user_id, "imp1", "opened"
        )

        assert match is not None
        # La senal queda registrada => proteccion anti-cleanup por
        # feedback_implicit (criterio `attached` de maintenance_tasks).
        assert match.feedback_implicit == [{"action": "opened"}]
        assert match.feedback is None  # el camino implicit no toca el explicito
        assert match.score_final == 0.0  # fila minima: el proximo run la rellena

    async def test_returns_none_when_job_missing(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Sin Job local no hay respaldo accionable: None => 404 del router."""
        user_id = await _register(client)
        assert (
            await _svc(db_session).record_implicit_feedback(user_id, "nope", "opened")
        ) is None

    async def test_appends_signal_to_existing_row(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        """Con fila existente el comportamiento previo se conserva (append)."""
        user_id = await _register(client)
        await _insert_job(db_session, "imp2")
        await _seed(
            db_session, user_id, "imp2", feedback_implicit=[{"action": "opened"}]
        )

        match = await _svc(db_session).record_implicit_feedback(
            user_id, "imp2", "view_time", duration_ms=12000
        )

        assert match is not None
        assert match.feedback_implicit == [
            {"action": "opened"},
            {"action": "view_time", "duration_ms": 12000},
        ]


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
