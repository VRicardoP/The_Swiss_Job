"""Materialización incremental del cross-encoder por watermark (P7-b).

Cadencia diaria en el NAS: puntúa los misses del corpus vigente dentro del
presupuesto predeclarado (≤60 min/ciclo) y, SOLO si queda al día
(remaining == 0), dispara la evaluación canónica — cuya valla F3 publica una
fotografía completa de una única generación o descarta. Con backlog, el feed
vigente sigue intacto y se emite la señal (log WARNING + resultado con
`status: "backlog"`, backlog y antigüedad) — jamás un feed parcial.

Convención del repo: `def` + asyncio.run(_impl()).
"""

import asyncio
import logging
from typing import Any

import sqlalchemy as sa

from jobhunt_core import matching
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory

logger = logging.getLogger(__name__)

# Presupuesto OPERATIVO por ciclo (predeclaración P7-b sellada 3d21bb4).
MATERIALIZE_BUDGET_SECONDS = 3600.0


@celery_app.task(name="jobhunt.matching.materialize_ce", bind=True,
                 max_retries=1)
def materialize_ce_task(self, profile_id: str, policy_id: str,
                        budget_seconds: float = MATERIALIZE_BUDGET_SECONDS
                        ) -> dict[str, Any]:
    try:
        return asyncio.run(_impl(profile_id, policy_id, budget_seconds))
    except Exception as exc:
        logger.error("materialize_ce %s falló: %s", profile_id, exc)
        raise self.retry(exc=exc, countdown=300)


async def _impl(profile_id: str, policy_id: str, budget_seconds: float,
                session_factory=None) -> dict[str, Any]:
    if session_factory is None:
        async with task_session_factory() as factory:
            return await _con_factory(factory, profile_id, policy_id,
                                      budget_seconds)
    return await _con_factory(session_factory, profile_id, policy_id,
                              budget_seconds)


async def _con_factory(factory, profile_id, policy_id, budget_seconds):
    async with factory() as session:
        model_id = await matching.canonical_model_id(session, profile_id)
    if model_id is None:
        return {"status": "sin_modelo", "profile_id": str(profile_id)}
    r = await matching.materialize_misses(
        factory, profile_id, model_id, policy_id,
        budget_seconds=budget_seconds,
    )
    resultado: dict[str, Any] = {"profile_id": str(profile_id), **r}
    if r["status"] == "ok":
        # Al día: la evaluación publica la fotografía completa (o la
        # descarta su valla). move_current SOLO si esta política es la
        # canónica AHORA — con el contrato 2026-09-04, un move_current sobre
        # política no canónica se DESCARTA entero; en sombra (pre-promoción)
        # lo correcto es registrar append-only sin mover el feed. La valla
        # F3 revalida la canonicidad bajo el lock de todas formas.
        async with factory() as session:
            canonica = (
                await session.execute(sa.text(
                    "SELECT id FROM scoring_policies WHERE active "
                    "ORDER BY name, prompt_version LIMIT 1"
                ))
            ).scalar_one_or_none()
        ev = await matching.evaluate_profile(
            factory, profile_id, model_id, policy_id,
            move_current=str(canonica) == str(policy_id),
        )
        resultado["evaluacion"] = {
            k: ev.get(k) for k in ("status", "evaluated", "moved_current")
        }
    return resultado
