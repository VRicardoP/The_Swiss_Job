"""Purga periódica de idempotency_records caducados (C-API-W 2º análisis).

El 2º análisis registró que la deferral de la purga estaba HUÉRFANA (se
atribuía a C-2, cuyo DoD no la cubre y opera sobre arpones del piloto, otro
codebase). Su sitio natural es el beat del core-worker: aquí. Cablearla de
raíz acota además la retención de PII — el `response` guardado de un PUT de
perfil incluye el cv_text, y sin purga quedaría indefinidamente; con el TTL
de 24h (IDEM_TTL) y este barrido cada hora, la ventana es acotada.

Convención del repo: `def` + asyncio.run(_impl()).
"""

import asyncio
import logging

from jobhunt_core.api.idempotency import purge_expired
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory

logger = logging.getLogger(__name__)


@celery_app.task(name="jobhunt.idempotency.purge_expired", bind=True, max_retries=1)
def purge_expired_task(self) -> dict:
    try:
        return asyncio.run(_purge_impl())
    except Exception as exc:
        logger.error("idempotency.purge_expired falló: %s", exc)
        raise self.retry(exc=exc, countdown=300)


async def _purge_impl() -> dict:
    async with task_session_factory() as session_factory:
        async with session_factory() as session:
            deleted = await purge_expired(session)
            await session.commit()
    return {"deleted": deleted}
