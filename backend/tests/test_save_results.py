"""Tests de caracterización para MatchService._save_results (UPSERT).

Fijan las 3 ramas antes de refactorizar: UPDATE (refresca scores, conserva
campos del usuario), INSERT (nuevo), PRUNE (borra huérfanas limpias, conserva
las que tienen engagement). Cubre lo que test_match.py NO ejercita (solo INSERT).
"""

import uuid
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.match_result import MatchResult
from services.match_service import MatchService
from tests.conftest import random_email

_PW = "TestPass123!"


async def _register(client: AsyncClient) -> uuid.UUID:
    email = random_email()
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _PW, "gdpr_consent": True},
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


async def _seed_result(db: AsyncSession, user_id: uuid.UUID, h: str, **over) -> None:
    mr = MatchResult(
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
    )
    for k, v in over.items():
        setattr(mr, k, v)
    db.add(mr)
    await db.commit()


def _result(h: str, score_final: float) -> dict:
    return {
        "job": SimpleNamespace(hash=h),
        "score_embedding": 0.5,
        "score_salary": 0.5,
        "score_location": 0.5,
        "score_recency": 0.5,
        "score_llm": 0.0,
        "score_final": score_final,
        "urgency_score": 0,
        "explanation": None,
        "matching_skills": [],
        "missing_skills": [],
    }


@pytest.mark.anyio
class TestSaveResultsUpsert:
    async def test_update_insert_prune_and_preserve(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user_id = await _register(client)
        for h in ("aaa", "bbb", "ccc", "ddd"):
            await _insert_job(db_session, h)

        # Estado previo: A con feedback, B "applied", C limpio (detected).
        await _seed_result(db_session, user_id, "aaa", feedback="thumbs_down")
        await _seed_result(db_session, user_id, "bbb", application_status="applied")
        await _seed_result(db_session, user_id, "ccc")

        # Nueva run: A (update) + D (insert). B y C NO aparecen.
        svc = MatchService(db_session)
        await svc._save_results(user_id, [_result("aaa", 77.0), _result("ddd", 60.0)])

        rows = (
            (
                await db_session.execute(
                    select(MatchResult).where(MatchResult.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        by_hash = {r.job_hash: r for r in rows}

        # A: UPDATE — score refrescado, feedback del usuario preservado.
        assert by_hash["aaa"].score_final == 77.0
        assert by_hash["aaa"].feedback == "thumbs_down"
        # D: INSERT.
        assert "ddd" in by_hash
        assert by_hash["ddd"].score_final == 60.0
        # B: KEPT (engagement) — score congelado (10.0), status conservado.
        assert "bbb" in by_hash
        assert by_hash["bbb"].score_final == 10.0
        assert by_hash["bbb"].application_status == "applied"
        # C: PRUNED (limpio, ya no en results).
        assert "ccc" not in by_hash

    async def test_empty_results_prunes_clean_keeps_engaged(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        user_id = await _register(client)
        for h in ("e1", "e2"):
            await _insert_job(db_session, h)
        await _seed_result(db_session, user_id, "e1", feedback="dismissed")
        await _seed_result(db_session, user_id, "e2")  # limpio

        svc = MatchService(db_session)
        await svc._save_results(user_id, [])  # sin resultados

        rows = (
            (
                await db_session.execute(
                    select(MatchResult).where(MatchResult.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
        hashes = {r.job_hash for r in rows}
        assert "e1" in hashes  # engagement → conservado
        assert "e2" not in hashes  # limpio → borrado
