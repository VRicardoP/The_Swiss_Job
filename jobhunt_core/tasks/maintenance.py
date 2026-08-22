"""Tareas de mantenimiento del core (cola core.default)."""

import asyncio

from jobhunt_core.archive import archive_sweep
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory


@celery_app.task(name="jobhunt.maintenance.archive_sweep")
def archive_sweep_task() -> dict:
    """Barrido de archivado ADR-07 (ver jobhunt_core/archive.py)."""
    return asyncio.run(_impl())


async def _impl() -> dict:
    async with task_session_factory() as factory:
        async with factory() as session:
            result = await archive_sweep(session)
            await session.commit()
            return result
