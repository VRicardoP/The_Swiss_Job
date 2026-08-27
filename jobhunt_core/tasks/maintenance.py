"""Tareas de mantenimiento del core (cola core.default)."""

import asyncio

from jobhunt_core.archive import archive_sweep
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory
from jobhunt_core.dedup import scan_semantic_candidates
from jobhunt_core.retention import purge_retention


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


@celery_app.task(name="jobhunt.maintenance.dedup_scan")
def dedup_scan_task() -> dict:
    """Generador de candidatos de dedup semántico (F-5, jobhunt_core/dedup.py)."""
    return asyncio.run(_run(scan_semantic_candidates))


async def _run(fn) -> dict:
    async with task_session_factory() as factory:
        async with factory() as session:
            result = await fn(session)
            await session.commit()
            return result
