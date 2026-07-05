"""MatchResultService — lectura y CRUD de resultados de matching por usuario.

Extraído de MatchService (SRP): MatchService orquesta el pipeline de matching;
este servicio sirve/edita los MatchResult persistidos (results, history, saved,
feedback explícito e implícito). Solo depende de la sesión de BD.
"""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.match_result import (
    NEGATIVE_FEEDBACK,
    POSITIVE_FEEDBACK,
    MatchResult,
)


class MatchResultService:
    """Consulta y edición de MatchResult para un usuario."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_results(
        self,
        user_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Get persisted match results with job details.

        Returns (results_with_jobs, total_count).

        Excluye los resultados con feedback negativo (thumbs_down/dismissed):
        una oferta marcada "not for me" deja de mostrarse de inmediato. El
        registro persiste en BD (sigue excluyendo ese hash de futuros runs por
        _get_excluded_hashes), y el job se elimina por expiración normal.
        """
        not_dismissed = or_(
            MatchResult.feedback.is_(None),
            MatchResult.feedback.not_in(NEGATIVE_FEEDBACK),
        )

        count_stmt = (
            select(func.count())
            .select_from(MatchResult)
            .where(MatchResult.user_id == user_id, not_dismissed)
        )
        total = (await self.db.execute(count_stmt)).scalar_one()

        stmt = (
            select(MatchResult, Job)
            .join(Job, MatchResult.job_hash == Job.hash)
            .where(MatchResult.user_id == user_id, not_dismissed)
            .order_by(MatchResult.score_final.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.db.execute(stmt)).all()

        results = [{"match": match, "job": job} for match, job in rows]
        return results, total

    async def submit_feedback(
        self,
        user_id: uuid.UUID,
        job_hash: str,
        feedback: str,
    ) -> MatchResult | None:
        """Record user feedback on a match result."""
        match = await self._get_one(user_id, job_hash)
        if match is None:
            return None
        match.feedback = feedback
        await self.db.commit()
        await self.db.refresh(match)
        return match

    async def clear_feedback(
        self,
        user_id: uuid.UUID,
        job_hash: str,
    ) -> MatchResult | None:
        """Elimina el feedback explícito de un resultado (lo pone a None)."""
        match = await self._get_one(user_id, job_hash)
        if match is None:
            return None
        match.feedback = None
        await self.db.commit()
        await self.db.refresh(match)
        return match

    async def get_saved_jobs(
        self,
        user_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Devuelve los empleos marcados como thumbs_up o applied."""
        count_stmt = (
            select(func.count())
            .select_from(MatchResult)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.feedback.in_(POSITIVE_FEEDBACK),
            )
        )
        total = (await self.db.execute(count_stmt)).scalar_one()

        stmt = (
            select(MatchResult, Job)
            .join(Job, MatchResult.job_hash == Job.hash)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.feedback.in_(POSITIVE_FEEDBACK),
            )
            .order_by(MatchResult.score_final.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.db.execute(stmt)).all()
        results = [{"match": match, "job": job} for match, job in rows]
        return results, total

    async def record_implicit_feedback(
        self,
        user_id: uuid.UUID,
        job_hash: str,
        action: str,
        duration_ms: int | None = None,
    ) -> MatchResult | None:
        """Record implicit feedback signal on a match result.

        Signals and their weights:
          opened -> +0.1, view_time (>10s) -> +0.2, saved -> +0.5,
          applied -> +1.0, dismissed -> -0.3, skipped -> -0.1
        """
        match = await self._get_one(user_id, job_hash)
        if match is None:
            return None

        signals = match.feedback_implicit or []
        signal = {"action": action}
        if duration_ms is not None:
            signal["duration_ms"] = duration_ms
        signals.append(signal)
        match.feedback_implicit = signals

        await self.db.commit()
        await self.db.refresh(match)
        return match

    async def _get_one(self, user_id: uuid.UUID, job_hash: str) -> MatchResult | None:
        """Carga el MatchResult (user_id, job_hash) o None."""
        stmt = select(MatchResult).where(
            MatchResult.user_id == user_id,
            MatchResult.job_hash == job_hash,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()
