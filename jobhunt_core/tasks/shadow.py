"""Tarea Celery del proyector de la sombra (B-02) — cola core.harvest.

SIN beat por contrato: se dispara manualmente o desde el runner de ciclos
(B-05) — aquí solo se REGISTRA. Convención del repo: `def` + asyncio.run.
"""

import asyncio
import logging
from typing import Any

from jobhunt_core.celery_app import celery_app
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
