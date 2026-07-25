"""Tareas Celery de la sombra: proyector (B-02, cola core.harvest) y
métricas/muestreo/purga (B-04, cola core.default).

COLAS (decisión B-04, justificada): las tareas de métricas van a
core.default y NO a core.harvest — son observabilidad y mantenimiento, no
ingesta: no tocan los locks del sink ni necesitan serializar con la
proyección, y el muestreador (cadencia 5 min, la cablea B-05) no puede
esperar detrás de un lote largo del proyector en core.harvest (con
prefetch=1 y acks_late un lote de proyección monopoliza esa cola).

SIN beat por contrato: se disparan manualmente o desde el runner de ciclos
(B-05) — aquí solo se REGISTRAN. Convención del repo: `def` + asyncio.run.
"""

import asyncio
import logging
from datetime import date
from typing import Any

from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory
from jobhunt_core.shadow import metrics
from jobhunt_core.shadow.projector import DEFAULT_BATCH_SIZE, project_pending

logger = logging.getLogger(__name__)


@celery_app.task(name="jobhunt.shadow.project", bind=True, max_retries=1)
def project_task(
    self, batch_size: int = DEFAULT_BATCH_SIZE, max_batches: int | None = None
) -> dict[str, Any]:
    try:
        return asyncio.run(
            project_pending(batch_size=batch_size, max_batches=max_batches)
        )
    except Exception as exc:
        # Transitorios (BD): retry único — el lote es atómico e idempotente
        # (applied_at sin sellar ⇒ la re-proyección no duplica nada).
        logger.error("shadow.project falló: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _in_session(fn, *args, **kwargs):
    """Impl común B-04: sesión desechable + commit (patrón task_session_factory)."""
    async with task_session_factory() as factory:
        async with factory() as session:
            result = await fn(session, *args, **kwargs)
            await session.commit()
    return result


@celery_app.task(name="jobhunt.shadow.sample_outbox_lag", bind=True, max_retries=1)
def sample_outbox_lag_task(self) -> dict[str, Any]:
    """Muestreador ligero del lag del outbox (§5: cada 5 min — la cadencia
    la cablea B-05). Un sample perdido es solo un punto menos del p99."""
    try:
        return asyncio.run(_in_session(metrics.sample_outbox_lag))
    except Exception as exc:
        logger.error("shadow.sample_outbox_lag falló: %s", exc)
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="jobhunt.shadow.compute_cycle", bind=True, max_retries=1)
def compute_cycle_task(
    self, cycle_id: str | None = None, legacy_schema: str = "public"
) -> dict[str, Any]:
    """Computa y persiste las 10 métricas de §5 del ciclo CERRADO más
    reciente (o el `cycle_id` ISO indicado). Idempotente (upsert por PK)."""
    try:
        cid = date.fromisoformat(cycle_id) if cycle_id else None
        return asyncio.run(
            _in_session(metrics.compute_cycle, cid, legacy_schema)
        )
    except Exception as exc:
        logger.error("shadow.compute_cycle falló: %s", exc)
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="jobhunt.shadow.purge_staging", bind=True, max_retries=1)
def purge_staging_task(self) -> dict[str, Any]:
    """Purga del staging aplicado (retención §2, asignada a B-04):
    idempotente y SIEMPRE preservando la última fila users por pk."""
    try:
        return asyncio.run(_in_session(metrics.purge_staging))
    except Exception as exc:
        logger.error("shadow.purge_staging falló: %s", exc)
        raise self.retry(exc=exc, countdown=120)
