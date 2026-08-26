"""Tarea Celery de despacho del outbox (A-10) — at-least-once por destino.

Convención del repo: `def` + asyncio.run(_impl()). El claim es transaccional
(SKIP LOCKED + lease); el transporte corre FUERA de la transacción del claim y
el RESULTADO de cada entrega se persiste EN CUANTO OCURRE — son los marks
quienes CONSUMEN el intento (G2-P3-4), así que ni un dispatcher en crash-loop
ni un lease caducado gastan intentos sin transporte. Sin transporte
configurado no se reclama nada.

G5-P2-2 — por qué el resultado se persiste por entrega y no al final del lote:
`claims` mide «reclamos sin RESULTADO PERSISTIDO», y con los marks al final un
payload que MATA al proceso en la posición K hacía que NINGUNO se ejecutara.
Los eventos 1..K-1 se transportaban CORRECTAMENTE en cada vuelta y aun así
llegaban al tope de reclamos junto al veneno: hasta 99 vecinos ENTREGADOS
retirados a DEAD-LETTER con el diagnóstico invertido. Persistiendo por entrega,
un vecino ya transportado vuelve a `claims = 0` en cuanto ocurre, así que solo
acumula reclamos sin resultado quien nunca llega a producirlo.
G6-N-1 — el alcance exacto de esta costura, que la redacción anterior
sobredimensionaba: solo salva a los vecinos ANTERIORES al veneno, y por el
`ORDER BY` del claim el veneno es la CABEZA de la cola, así que normalmente no
hay ninguno. Lo que acota el radio en el caso base es el `LIMIT 1` de
`retire_poisoned` (una retirada por ciclo, la cabeza); persistir por entrega
sigue siendo necesario y correcto, pero para el veneno en posición K > 0, que
es cuando sí hay 1..K-1 vecinos que salvar.
G2-H-7: la MISMA costura renueva el lease del resto del lote.

P1-1 (rev. externa parte 2): en el ARRANQUE del worker (señal
worker_process_init — cada proceso del pool, jamás en tests con `.apply()`)
se registra el transporte SOMBRA (shadow/inbox.py → jobhunt.shadow_inbox)
SOLO si nadie inyectó otro: la entrega deja de ser un no-op permanente en
Fase B, y los tests siguen inyectando el suyo con set_transport. La cadencia
(beat cada 5 min) vive en celery_app.py.
"""

import asyncio
import logging
import time
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
            "poisoned": 0, "fenced_out": 0, "lease_renewals": 0,
            "lease_overrun": 0, "no_transport": True,
        }

    async with task_session_factory() as session_factory:
        async with session_factory() as session:
            # G3-P2-2: antes de reclamar, retirar a DEAD-LETTER lo que ya
            # agotó intentos REALES y nadie posee (lease caducado) — con un
            # transporte que sistemáticamente supera el lease, el mark del
            # dueño superado siempre cae por el fence y la transición a 'dead'
            # no llegaba a escribirse nunca.
            retired = await delivery.retire_exhausted(session)
            # G3-H-1: y el VENENO — reclamado una y otra vez sin producir
            # jamás un resultado (mata al proceso antes de marcar), con la
            # cabeza de la cola secuestrada mientras siga vivo.
            poisoned = await delivery.retire_poisoned(session)
            claimed, lease_token = await delivery.claim_deliveries(session, limit=limit)
            await session.commit()
        if not claimed:
            return {
                "claimed": 0, "delivered": 0, "failed": 0,
                "dead": retired + poisoned, "poisoned": poisoned,
                "fenced_out": 0, "lease_renewals": 0, "lease_overrun": 0,
                "no_transport": False,
            }

        delivered_n, retried_n, dead_n = 0, 0, 0
        lease_renewals, lease_overrun = 0, 0
        desde = time.monotonic()
        async with session_factory() as session:
            for idx, row in enumerate(claimed):
                # Transporte FUERA de la transacción del claim: un cuelgue deja
                # el lease y el evento se re-reclama al caducar (at-least-once).
                ok: list[dict[str, Any]] = []
                ko: list[dict[str, Any]] = []
                try:
                    transport(row.destination, delivery.event_dict(row))
                    ok.append({"eid": row.event_id, "dest": row.destination})
                except Exception as exc:
                    ko.append(
                        {
                            "eid": row.event_id, "dest": row.destination,
                            "attempts": row.attempts + 1, "error": str(exc)[:500],
                        }
                    )
                # G5-P2-2: el resultado se persiste AQUÍ, no al final del lote.
                hechas = await delivery.mark_delivered(session, ok, lease_token)
                fallos = await delivery.mark_failed(session, ko, lease_token)
                await session.commit()
                delivered_n += hechas
                retried_n += fallos["retried"]
                dead_n += fallos["dead"]
                if ok and not hechas:
                    # Transporte EJECUTADO cuyo resultado no pudo persistirse:
                    # la fila ya la resolvió otro dueño (G5-P3-3, en ORIGEN).
                    lease_overrun += 1
                # G2-H-7: renovar el lease del RESTO antes de agotarlo.
                # G6-P2-2: solo si QUEDA resto. `renew_lease` sale por su
                # early-return sin renovar nada cuando la cola está vacía (el
                # último elemento del lote), y contar esa vuelta como
                # renovación hacía que `lease_renewals` avisara de un
                # desbordamiento inexistente en lotes de milisegundos.
                resto = claimed[idx + 1:]
                if resto and time.monotonic() - desde >= delivery.LEASE_RENEW_AFTER_S:
                    lease_token, perdidas = await delivery.renew_lease(
                        session, resto, lease_token
                    )
                    await session.commit()
                    lease_renewals += 1
                    lease_overrun += perdidas
                    desde = time.monotonic()
    real = delivered_n + dead_n + retried_n
    return {
        "claimed": len(claimed),
        "delivered": delivered_n,
        "failed": retried_n,
        "dead": dead_n + retired + poisoned,
        "poisoned": poisoned,
        # Marks que el fence descartó (claim superado): observabilidad.
        "fenced_out": len(claimed) - real,
        # G5-P3-3: el desbordamiento del lease contado en ORIGEN, no por
        # diferencia — `fenced_out` dejó de delatarlo cuando el éxito salió del
        # fence, y `claims = 0` borraba el otro rastro. G6-P2-2 — qué dicen de
        # verdad: `lease_renewals > 0` dice que el lote superó
        # LEASE_RENEW_AFTER_S (= LEASE_S/2, el punto de renovación, NO LEASE_S)
        # con entregas todavía por transportar; `lease_overrun > 0`, que además
        # perdimos filas o resultados por ello (G2-H-7 en vivo).
        "lease_renewals": lease_renewals,
        "lease_overrun": lease_overrun,
        "no_transport": False,
    }
