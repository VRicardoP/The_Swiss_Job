"""Tarea Celery de cosecha por scope (A-04) — cola core.harvest.

Se cablea AHORA que existe el sink real (RawListingSink): con un sink no-op el
estado del scope avanzaría sin persistir. Convención del repo: tareas con
`def` + asyncio.run(_impl()).
"""

import asyncio
import logging
from typing import Any

import httpx
import sqlalchemy as sa

from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import SessionLocal
from jobhunt_core.harvest.providers import get_provider
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import ScopeRunResult

logger = logging.getLogger(__name__)


@celery_app.task(name="jobhunt.harvest.run_scope", bind=True, max_retries=1)
def run_scope_task(self, scope_id: str) -> dict[str, Any]:
    try:
        result = asyncio.run(_run_scope_impl(scope_id))
    except Exception as exc:
        logger.error("harvest.run_scope %s falló: %s", scope_id, exc)
        raise self.retry(exc=exc, countdown=120)
    if result.status == "error":
        # 'stale'/'skipped'/'partial' NO se reintentan (no son fallos de fuente).
        raise self.retry(
            exc=RuntimeError(result.error or "run error"), countdown=120
        )
    return {
        "scope_id": result.scope_id,
        "status": result.status,
        "listings": result.listings,
        "pages": result.pages,
    }


async def _run_scope_impl(scope_id: str) -> ScopeRunResult:
    async with SessionLocal() as session:
        source_name = (
            await session.execute(
                sa.text(
                    "SELECT s.name FROM harvest_scopes hs "
                    "JOIN sources s ON s.id = hs.source_id WHERE hs.id = :sid"
                ),
                {"sid": scope_id},
            )
        ).scalar_one_or_none()
    if source_name is None:
        return ScopeRunResult(scope_id=scope_id, status="error", error="scope inexistente")
    provider = get_provider(source_name)
    async with httpx.AsyncClient() as http:
        return await run_scope(scope_id, provider, RawListingSink(), http)
