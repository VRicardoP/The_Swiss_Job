"""MatchResultService — lectura y CRUD de resultados de matching por usuario.

Extraído de MatchService (SRP): MatchService orquesta el pipeline de matching;
este servicio sirve/edita los MatchResult persistidos (results, history, saved,
feedback explícito e implícito). Solo depende de la sesión de BD.
"""

import uuid

from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
        _get_excluded_hashes). Solo se muestran ofertas ACTIVAS: las caducadas se
        archivan (is_active=False, ver cleanup_stale_jobs) y no reaparecen aquí.
        """
        not_dismissed = or_(
            MatchResult.feedback.is_(None),
            MatchResult.feedback.not_in(NEGATIVE_FEEDBACK),
        )

        count_stmt = (
            select(func.count())
            .select_from(MatchResult)
            .join(Job, MatchResult.job_hash == Job.hash)
            .where(
                MatchResult.user_id == user_id,
                not_dismissed,
                Job.is_active.is_(True),
                Job.duplicate_of.is_(None),
            )
        )
        total = (await self.db.execute(count_stmt)).scalar_one()

        stmt = (
            select(MatchResult, Job)
            .join(Job, MatchResult.job_hash == Job.hash)
            .where(
                MatchResult.user_id == user_id,
                not_dismissed,
                Job.is_active.is_(True),
                Job.duplicate_of.is_(None),
            )
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
        """Record user feedback on a match result.

        Huerfano legacy (existe el Job local pero NO la fila MatchResult —
        p.ej. visible via feed del core, criterio unificador en
        services/matching/seam.py): se upserta una fila MINIMA con el
        feedback para que "not for me" desaparezca tambien ahi. Solo si el
        Job tampoco existe se devuelve None (404 del router).
        """
        match = await self._get_one(user_id, job_hash)
        if match is not None:
            match.feedback = feedback
            await self.db.commit()
            await self.db.refresh(match)
            return match
        return await self._upsert_minimal_feedback(
            user_id, job_hash, {"feedback": feedback}
        )

    async def _upsert_minimal_feedback(
        self,
        user_id: uuid.UUID,
        job_hash: str,
        values: dict,
    ) -> MatchResult | None:
        """Upsert de una fila minima para un Job existente.

        Camino COMUN del feedback explicito e implicito sobre huerfanos
        legacy (Job local SIN fila MatchResult, visibles via feed del core).
        Upsert real (ON CONFLICT sobre uq_match_user_job): una carrera con el
        motor de matching o con otra peticion no rompe la unicidad. Scores a
        0.0 y skills vacias: el proximo run de matching los rellenara;
        `values` (feedback o feedback_implicit) es lo unico que este camino
        escribe. Semantica por clave (P2 lost update, rev. externa A.SEAM):
        - feedback (escalar): se PISA — ultimo gesto explicito gana.
        - feedback_implicit (lista de señales): se CONCATENA de forma
          ATOMICA en SQL (COALESCE(...,'[]'::jsonb) || excluded...) tanto en
          la fila existente como en la carrera de dos altas huerfanas — un
          read-modify-write en Python perderia señales concurrentes.
        """
        job_exists = (
            await self.db.execute(select(Job.hash).where(Job.hash == job_hash))
        ).scalar_one_or_none()
        if job_exists is None:
            return None
        stmt = pg_insert(MatchResult).values(
            user_id=user_id,
            job_hash=job_hash,
            score_embedding=0.0,
            score_salary=0.0,
            score_location=0.0,
            score_recency=0.0,
            score_llm=0.0,
            score_final=0.0,
            matching_skills=[],
            missing_skills=[],
            **values,
        )
        set_ = {
            key: (
                func.coalesce(
                    MatchResult.__table__.c.feedback_implicit,
                    cast("[]", JSONB),
                ).op("||")(stmt.excluded.feedback_implicit)
                if key == "feedback_implicit"
                else stmt.excluded[key]
            )
            for key in values
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "job_hash"],
            set_=set_,
        )
        await self.db.execute(stmt)
        await self.db.commit()
        return await self._get_one(user_id, job_hash)

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

        Huerfano legacy (Job local SIN fila MatchResult, visible via feed
        del core): mismo camino de upsert minimo que el feedback explicito
        (simetria implicit/explicit — 2ª rev. A.SEAM matching). Solo si el
        Job tampoco existe se devuelve None (404 del router). La fila creada
        con feedback_implicit queda ademas protegida del cleanup de ofertas
        caducadas (criterio `attached` de maintenance_tasks).

        UN SOLO camino ATOMICO (P2 lost update, rev. externa A.SEAM): el
        upsert concatena la señal en SQL (COALESCE(...,'[]'::jsonb) ||
        excluded...) tanto si la fila existe como si es alta huerfana — el
        read-modify-write anterior (leer lista, append, commit) perdia
        señales con dos sesiones concurrentes (ultimo commit pisaba). La
        existencia del Job cubre ambos casos: una fila MatchResult sin Job
        es imposible (FK job_hash).
        """
        signal = {"action": action}
        if duration_ms is not None:
            signal["duration_ms"] = duration_ms
        return await self._upsert_minimal_feedback(
            user_id, job_hash, {"feedback_implicit": [signal]}
        )

    async def _get_one(self, user_id: uuid.UUID, job_hash: str) -> MatchResult | None:
        """Carga el MatchResult (user_id, job_hash) o None.

        populate_existing: las señales implicitas se escriben con SQL atomico
        (fuera del ORM) — sin esto, una instancia ya presente en la identity
        map de la sesion se devolveria con el estado ANTERIOR al upsert."""
        stmt = (
            select(MatchResult)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.job_hash == job_hash,
            )
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()
