"""Tarea Celery de despacho del outbox (A-10) — at-least-once por destino.

Convención del repo: `def` + asyncio.run(_impl()). El claim es transaccional
(SKIP LOCKED + lease); el transporte corre FUERA de la transacción del claim y
los marks van en lotes — que son quienes CONSUMEN el intento (G2-P3-4), así que
ni un dispatcher en crash-loop ni un lease caducado gastan intentos sin
transporte. Sin transporte configurado no se reclama nada.

P1-1 (rev. externa parte 2): en el ARRANQUE del worker (señal
worker_process_init — cada proceso del pool, jamás en tests con `.apply()`)
se registra el transporte SOMBRA (shadow/inbox.py → jobhunt.shadow_inbox)
SOLO si nadie inyectó otro: la entrega deja de ser un no-op permanente en
Fase B, y los tests siguen inyectando el suyo con set_transport. La cadencia
(beat cada 5 min) vive en celery_app.py.
"""

import asyncio
import logging
from typing import Any

from celery.signals import worker_process_init

from jobhunt_core import delivery
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory

logger = logging.getLogger(__name__)


@worker_process_init.connect
def register_shadow_inbox_transport(**_kwargs) -> None:
    """Arranque del worker: transporte sombra por defecto (P1-1b, §8).

    `register_if_unset` respeta cualquier transporte YA inyectado (tests,
    o el HTTP real que llega en Fase C)."""
    from jobhunt_core.shadow import inbox

    inbox.register_if_unset()


@celery_app.task(name="jobhunt.delivery.dispatch_outbox", bind=True, max_retries=1)
def dispatch_outbox_task(self, limit: int = 100) -> dict[str, Any]:
    try:
        return asyncio.run(_dispatch_impl(limit))
    except Exception as exc:
        logger.error("delivery.dispatch_outbox falló: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _dispatch_impl(limit: int) -> dict[str, Any]:
    # Sin transporte NO se reclama nada (2ª rev. A-10): reclamar sin poder
    # entregar solo movería estado y leases para nada.
    transport = delivery.get_transport()
    if transport is None:
        logger.warning(
            "delivery: sin transporte configurado — no se reclama ninguna entrega"
        )
        return {
            "claimed": 0, "delivered": 0, "failed": 0, "dead": 0,
            "fenced_out": 0, "no_transport": True,
        }

    async with task_session_factory() as session_factory:
        async with session_factory() as session:
            # G3-P2-2: antes de reclamar, retirar a DEAD-LETTER lo que ya
            # agotó intentos REALES y nadie posee (lease caducado) — con un
            # transporte que sistemáticamente supera el lease, el mark del
            # dueño superado siempre cae por el fence y la transición a 'dead'
            # no llegaba a escribirse nunca.
            retired = await delivery.retire_exhausted(session)
            claimed, lease_token = await delivery.claim_deliveries(session, limit=limit)
            await session.commit()
        if not claimed:
            return {
                "claimed": 0, "delivered": 0, "failed": 0, "dead": retired,
                "fenced_out": 0, "no_transport": False,
            }

        delivered, failed = [], []
        for row in claimed:
            # Transporte FUERA de la transacción del claim: un cuelgue deja el
            # lease y el evento se re-reclama al caducar (at-least-once); el
            # FENCING por lease impide que nuestros marks tardíos pisen al
            # nuevo dueño — y sus alertas/contadores solo cuentan transiciones
            # REALES (2ª rev.).
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
            delivered_real = await delivery.mark_delivered(session, delivered, lease_token)
            fail_result = await delivery.mark_failed(session, failed, lease_token)
            await session.commit()
    real = delivered_real + fail_result["dead"] + fail_result["retried"]
    return {
        "claimed": len(claimed),
        "delivered": delivered_real,
        "failed": fail_result["retried"],
        "dead": fail_result["dead"] + retired,
        # Marks que el fence descartó (claim superado): observabilidad.
        "fenced_out": len(claimed) - real,
        "no_transport": False,
    }
