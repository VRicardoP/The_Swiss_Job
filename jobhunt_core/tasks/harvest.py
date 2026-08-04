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
from jobhunt_core.database import task_session_factory
from jobhunt_core.harvest.provider import ProviderConfigError
from jobhunt_core.harvest.providers import UnknownProviderError, get_provider
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import ScopeRunResult

logger = logging.getLogger(__name__)


@celery_app.task(name="jobhunt.harvest.run_scope", bind=True, max_retries=1)
def run_scope_task(self, scope_id: str) -> dict[str, Any]:
    try:
        result = asyncio.run(_run_scope_impl(scope_id))
    except (UnknownProviderError, ProviderConfigError) as exc:
        # Config PERMANENTE (provider desconocido / params inválidos, rev. 2ª
        # #3): excepciones CONCRETAS — un KeyError interno cualquiera no debe
        # clasificarse como configuración. Falla explícito SIN retry.
        logger.error("harvest.run_scope %s: config inválida: %s — sin retry", scope_id, exc)
        raise
    except Exception as exc:
        # Transitorios (HTTP, BD): AQUÍ sí retry.
        logger.error("harvest.run_scope %s falló: %s", scope_id, exc)
        raise self.retry(exc=exc, countdown=120)
    if result.status == "error":
        # 'stale'/'skipped'/'partial'/'not_found' NO se reintentan (no son
        # fallos de fuente).
        raise self.retry(
            exc=RuntimeError(result.error or "run error"), countdown=120
        )
    return {
        "scope_id": result.scope_id,
        "status": result.status,
        "listings": result.listings,
        "pages": result.pages,
    }


@celery_app.task(name="jobhunt.harvest.run_all", bind=True, max_retries=1)
def run_all_task(self, run_key: str) -> dict[str, Any]:
    """Orquestador del RUN de cosecha (A-11): idempotente por run_key — el
    reintento reutiliza el mismo harvest_run (id determinista) y SALTA los
    scopes ya terminados con éxito; solo re-ejecuta errores/colgados."""
    try:
        return asyncio.run(_run_all_impl(run_key))
    except Exception as exc:
        logger.error("harvest.run_all %s falló: %s", run_key, exc)
        raise self.retry(exc=exc, countdown=120)


async def _run_all_impl(run_key: str) -> dict[str, Any]:
    from jobhunt_core import runs

    results: dict[str, str] = {}
    executed = skipped = 0
    async with task_session_factory() as session_factory:
        async with session_factory() as session:
            run_id = await runs.start_run(session, run_key)
            scope_ids = (
                await session.execute(
                    sa.text("SELECT id FROM harvest_scopes WHERE enabled ORDER BY id")
                )
            ).scalars().all()
            await session.commit()
        for scope_id in scope_ids:
            async with session_factory() as session:
                token = await runs.claim_scope_run(session, run_id, scope_id)
                await session.commit()
            if token is None:
                # Ya hecho en este run — o en marcha por OTRO worker (claim
                # atómico con lease): en ambos casos NO se duplica.
                skipped += 1
                results[str(scope_id)] = "skipped"
                continue
            try:
                result = await _run_scope_impl(str(scope_id))
                status = result.status
            except Exception as exc:  # el run sigue con el resto de scopes
                logger.warning("run %s: scope %s falló: %s", run_key, scope_id, exc)
                status = "error"
            executed += 1
            async with session_factory() as session:
                # Fencing: si el lease venció y OTRO worker re-armó el scope, finish devuelve
                # False (no sobrescribe el estado ajeno) — se registra pero no cuenta como cierre.
                closed = await runs.finish_scope_run(session, run_id, scope_id, status, token)
                await session.commit()
            results[str(scope_id)] = status if closed else "superseded"
        async with session_factory() as session:
            overall = await runs.finish_run(session, run_id)
            await session.commit()
    return {
        "run_id": str(run_id), "status": overall,
        "executed": executed, "skipped": skipped, "scopes": results,
    }


async def _run_scope_impl(scope_id: str) -> ScopeRunResult:
    # Engine DESECHABLE por invocación (rev. 2ª #1): cada asyncio.run crea un
    # loop nuevo — el engine global quedaría ligado al primero y la segunda
    # tarea del proceso worker moriría ('Future attached to a different loop').
    async with task_session_factory() as session_factory:
        async with session_factory() as session:
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
            # Scope eliminado tras encolar la tarea: caso NORMAL y permanente
            # (rev. A-04 #5) — no es error de fuente y no debe consumir retry.
            return ScopeRunResult(
                scope_id=scope_id, status="not_found",
                detail={"reason": "scope inexistente"},
            )
        provider = get_provider(source_name)
        async with httpx.AsyncClient() as http:
            return await run_scope(
                scope_id, provider, RawListingSink(), http,
                session_factory=session_factory,
            )
