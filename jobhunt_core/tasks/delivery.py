"""Tarea Celery de despacho del outbox (A-10) — at-least-once por destino.

Convención del repo: `def` + asyncio.run(_impl()). El claim es transaccional
(SKIP LOCKED + lease + attempts); el transporte corre FUERA de la transacción
del claim y los marks van en lotes. Sin transporte configurado los eventos se
conservan pending sin consumir intentos.
"""

import asyncio
import logging
from typing import Any

from jobhunt_core import delivery
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory

logger = logging.getLogger(__name__)


@celery_app.task(name="jobhunt.delivery.dispatch_outbox", bind=True, max_retries=1)
def dispatch_outbox_task(self, limit: int = 100) -> dict[str, Any]:
    try:
        return asyncio.run(_dispatch_impl(limit))
    except Exception as exc:
        logger.error("delivery.dispatch_outbox falló: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _dispatch_impl(limit: int) -> dict[str, Any]:
    async with task_session_factory() as session_factory:
        async with session_factory() as session:
            claimed, lease_token = await delivery.claim_deliveries(session, limit=limit)
            await session.commit()
        if not claimed:
            return {"claimed": 0, "delivered": 0, "failed": 0, "dead": 0, "skipped": 0}

        transport = delivery.get_transport()
        if transport is None:
            logger.warning(
                "delivery: sin transporte configurado — %d entregas vuelven a "
                "pending sin consumir intento", len(claimed),
            )
            async with session_factory() as session:
                await delivery.release_unclaimed(session, claimed, lease_token)
                await session.commit()
            return {
                "claimed": len(claimed), "delivered": 0, "failed": 0,
                "dead": 0, "skipped": len(claimed),
            }

        delivered, failed = [], []
        for row in claimed:
            # Transporte FUERA de la transacción del claim: un cuelgue deja el
            # lease y el evento se re-reclama al caducar (at-least-once); el
            # FENCING por lease impide que nuestros marks tardíos pisen al
            # nuevo dueño.
            try:
                transport(row.destination, delivery.event_dict(row))
                delivered.append({"eid": row.event_id, "dest": row.destination})
            except Exception as exc:
                failed.append(
                    {
                        "eid": row.event_id, "dest": row.destination,
                        "attempts": row.attempts + 1, "error": str(exc)[:500],
                    }
                )
        async with session_factory() as session:
            await delivery.mark_delivered(session, delivered, lease_token)
            dead = await delivery.mark_failed(session, failed, lease_token)
            await session.commit()
    return {
        "claimed": len(claimed), "delivered": len(delivered),
        "failed": len(failed) - dead, "dead": dead, "skipped": 0,
    }
