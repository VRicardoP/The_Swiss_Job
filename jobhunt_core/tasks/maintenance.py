"""Tareas de mantenimiento del core (cola core.default)."""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from jobhunt_core.archive import archive_sweep
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory
from jobhunt_core.config import settings
from jobhunt_core.dedup import scan_semantic_candidates_batch
from jobhunt_core.retention import purge_retention

logger = logging.getLogger(__name__)


@celery_app.task(name="jobhunt.maintenance.archive_sweep")
def archive_sweep_task() -> dict:
    """Barrido de archivado ADR-07 (ver jobhunt_core/archive.py)."""
    return asyncio.run(_run(archive_sweep))


@celery_app.task(name="jobhunt.maintenance.dedup_lex_backfill")
def dedup_lex_backfill_task() -> dict:
    """One-shot post-deploy del Track R (P1-1 revisión): backfill LÉXICO
    completo — el beat solo cubre 48 h y el corpus antiguo (holdout incl.)
    jamás recibiría candidatos léxicos. Lanzar UNA vez tras desplegar."""
    from jobhunt_core.dedup import lexical_backfill

    # asyncio.run OBLIGATORIO (re-confirmación Track R, P1-A: sin él la
    # tarea devolvía la CORRUTINA sin ejecutar y el backfill nunca corría).
    return {"candidatos": asyncio.run(_run(lexical_backfill))}


@celery_app.task(name="jobhunt.maintenance.dedup_revalidate_by_rule")
def dedup_revalidate_by_rule_task(apply: bool = False) -> dict:
    """One-shot AUDITABLE (revisión FASE 2 P2-1): revalida los candidatos
    pendientes contra la regla de ubicación ratificada. Por defecto PREVIEW
    (cuenta + hash de ids, sin escribir); con apply=True aplica con
    procedencia y devuelve el mismo resumen para comparar."""
    from functools import partial

    from jobhunt_core.dedup import revalidate_pending_candidates

    return asyncio.run(_run(partial(revalidate_pending_candidates, apply=apply)))


@celery_app.task(name="jobhunt.maintenance.purge_retention")
def purge_retention_task() -> dict:
    """Retención de las tablas de trabajo terminado (O-4, jobhunt_core/retention.py)."""
    return asyncio.run(_run(purge_retention))


def _aware_timestamp(value: str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{field} debe incluir zona horaria")
    return parsed


async def _run_dedup_fragment(
    window_start: str | None = None,
    window_end: str | None = None,
    after_created_at: str | None = None,
    after_id: str | None = None,
) -> dict:
    if (window_start is None) != (window_end is None):
        raise ValueError("ventana dedup incompleta")
    if (after_created_at is None) != (after_id is None):
        raise ValueError("cursor dedup incompleto")

    if window_end is None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=int(settings.CORE_DEDUP_SCAN_WINDOW_H))
    else:
        start = _aware_timestamp(window_start, "window_start")
        end = _aware_timestamp(window_end, "window_end")
    cursor_at = (
        _aware_timestamp(after_created_at, "after_created_at")
        if after_created_at is not None
        else None
    )
    cursor_id = uuid.UUID(after_id) if after_id is not None else None

    async with task_session_factory() as factory:
        async with factory() as session:
            result = await scan_semantic_candidates_batch(
                session,
                window_start=start,
                window_end=end,
                after_created_at=cursor_at,
                after_id=cursor_id,
            )
            await session.commit()
    return {
        **result,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
    }


@celery_app.task(name="jobhunt.maintenance.dedup_scan", bind=True, max_retries=3)
def dedup_scan_task(
    self,
    window_start: str | None = None,
    window_end: str | None = None,
    after_created_at: str | None = None,
    after_id: str | None = None,
) -> dict:
    """Persist one bounded dedup fragment and enqueue its continuation."""
    try:
        result = asyncio.run(
            _run_dedup_fragment(
                window_start,
                window_end,
                after_created_at,
                after_id,
            )
        )
        cursor = result.get("next_cursor")
        if cursor is not None:
            dedup_scan_task.apply_async(
                kwargs={
                    "window_start": result["window_start"],
                    "window_end": result["window_end"],
                    "after_created_at": cursor["created_at"],
                    "after_id": cursor["vacancy_id"],
                },
                countdown=1,
            )
            result["continuation_enqueued"] = True
        return result
    except Exception as exc:
        logger.error("dedup_scan fragment failed: %s", exc)
        raise self.retry(exc=exc, countdown=60) from exc


async def _run(fn) -> dict:
    async with task_session_factory() as factory:
        async with factory() as session:
            result = await fn(session)
            await session.commit()
            return result
