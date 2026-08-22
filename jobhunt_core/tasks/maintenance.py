"""Tareas de mantenimiento del core (cola core.default)."""

import asyncio

from jobhunt_core.archive import archive_sweep
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory
from jobhunt_core.dedup import scan_semantic_candidates


@celery_app.task(name="jobhunt.maintenance.archive_sweep")
def archive_sweep_task() -> dict:
    """Barrido de archivado ADR-07 (ver jobhunt_core/archive.py)."""
    return asyncio.run(_run(archive_sweep))


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
