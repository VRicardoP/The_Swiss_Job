"""Entrega at-least-once del outbox POR destino (A-10, ADR-06).

- La EMISIÓN va en la misma transacción que la escritura (matching, ADR-05);
  aquí vive el DESPACHO: claim con `FOR UPDATE SKIP LOCKED` + lease (un lease
  caducado se re-reclama: at-least-once real), backoff exponencial por
  intento, DEAD-LETTER con ALERTA al agotar los intentos.
- El INBOX vive en la BD de CADA consumidor (BFF; NO en el core) y desduplica
  por (consumer_id, event_id) — contrato de transporte at-least-once +
  consumo idempotente. El TRANSPORTE es una costura inyectable:
  `set_transport(fn)` con fn(destination, event_dict) síncrono que lanza si
  falla; la implementación real (HTTP al inbox del BFF) llega con el cutover
  de Fase C — sin transporte configurado los pendientes se conservan SIN
  consumir intentos.
"""

import logging

import sqlalchemy as sa

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
BACKOFF_BASE_S = 60
BACKOFF_CAP_S = 3600
LEASE_S = 120

_transport = None


def set_transport(fn) -> None:
    """fn(destination: str, event: dict) — síncrono; excepción = fallo."""
    global _transport
    _transport = fn


def get_transport():
    return _transport


def backoff_seconds(attempts: int) -> int:
    return min(BACKOFF_BASE_S * (2 ** max(attempts - 1, 0)), BACKOFF_CAP_S)


async def claim_deliveries(session, limit: int = 100) -> tuple[list, object]:
    """Reclama entregas elegibles: pending vencidas o inflight con lease
    CADUCADO (el productor murió sin marcar: se re-entrega — at-least-once).
    SKIP LOCKED: varios dispatchers no se pisan. Incrementa attempts y toma
    el lease en el MISMO claim.

    Devuelve (rows, lease_token): el lease es el TOKEN DE FENCING (auditoría
    A-10) — un único timestamp por lote, calculado en BD; los marks solo
    escriben si el lease sigue siendo EL SUYO (un claim superado por re-claim
    jamás pisa el estado del nuevo dueño ni resucita un estado terminal)."""
    rows = (
        await session.execute(
            sa.text(
                "SELECT d.event_id, d.destination, d.attempts, "
                "o.type, o.aggregate, o.aggregate_id, o.subject_profile_id, "
                "o.version, o.payload "
                "FROM integration_outbox_deliveries d "
                "JOIN integration_outbox o ON o.event_id = d.event_id "
                "WHERE (d.state = 'pending' "
                "       AND (d.next_attempt_at IS NULL "
                "            OR d.next_attempt_at <= clock_timestamp())) "
                "   OR (d.state = 'inflight' AND d.lease < clock_timestamp()) "
                "ORDER BY d.next_attempt_at ASC NULLS FIRST "
                "LIMIT :n "
                "FOR UPDATE OF d SKIP LOCKED"
            ),
            {"n": limit},
        )
    ).all()
    if not rows:
        return [], None
    lease_token = (
        await session.execute(
            sa.text(
                "SELECT clock_timestamp() + "
                f"make_interval(secs => {int(LEASE_S)})"
            )
        )
    ).scalar_one()
    await session.execute(
        sa.text(
            "UPDATE integration_outbox_deliveries "
            "SET state = 'inflight', attempts = attempts + 1, lease = :lease "
            "WHERE event_id = :eid AND destination = :dest"
        ),
        [
            {"eid": r.event_id, "dest": r.destination, "lease": lease_token}
            for r in rows
        ],
    )
    return rows, lease_token


# Guarda de FENCING común: solo escribe quien aún posee el claim.
_FENCE = "AND state = 'inflight' AND lease = :lease"


async def mark_delivered(session, marks: list, lease_token) -> None:
    """marks = [{'eid', 'dest'}]. Solo si el claim sigue siendo NUESTRO."""
    if not marks:
        return
    await session.execute(
        sa.text(
            "UPDATE integration_outbox_deliveries "
            "SET state = 'delivered', ack_at = clock_timestamp(), lease = NULL "
            f"WHERE event_id = :eid AND destination = :dest {_FENCE}"
        ),
        [{**m, "lease": lease_token} for m in marks],
    )


async def mark_failed(session, fails: list, lease_token) -> int:
    """fails = [{'eid', 'dest', 'attempts', 'error'}] (attempts YA
    incrementado por el claim). Con fencing: un mark TARDÍO de un claim
    superado no toca nada (ni resucita delivered/dead). Devuelve cuántas
    pasaron a DEAD (alerta)."""
    if not fails:
        return 0
    dead = [f for f in fails if f["attempts"] >= MAX_ATTEMPTS]
    retry = [f for f in fails if f["attempts"] < MAX_ATTEMPTS]
    for f in dead:
        # ALERTA persistente del contrato (DoD A-10: dead-letter + alerta).
        logger.error(
            "delivery: evento %s → %s en DEAD-LETTER tras %d intentos (%s)",
            f["eid"], f["dest"], f["attempts"], (f["error"] or "")[:200],
        )
    if dead:
        await session.execute(
            sa.text(
                "UPDATE integration_outbox_deliveries "
                "SET state = 'dead', last_error = :error, lease = NULL "
                f"WHERE event_id = :eid AND destination = :dest {_FENCE}"
            ),
            [
                {"eid": f["eid"], "dest": f["dest"], "error": f["error"],
                 "lease": lease_token}
                for f in dead
            ],
        )
    if retry:
        await session.execute(
            sa.text(
                "UPDATE integration_outbox_deliveries "
                "SET state = 'pending', last_error = :error, lease = NULL, "
                "ack_at = NULL, "
                "next_attempt_at = clock_timestamp() + make_interval(secs => :backoff) "
                f"WHERE event_id = :eid AND destination = :dest {_FENCE}"
            ),
            [
                {
                    "eid": f["eid"], "dest": f["dest"], "error": f["error"],
                    "backoff": backoff_seconds(f["attempts"]),
                    "lease": lease_token,
                }
                for f in retry
            ],
        )
    return len(dead)


async def release_unclaimed(session, rows: list, lease_token) -> None:
    """Sin transporte configurado: devolver a pending SIN consumir el intento
    (no es un fallo del destino) y con espera para no ciclar. Con fencing."""
    if not rows:
        return
    await session.execute(
        sa.text(
            "UPDATE integration_outbox_deliveries "
            "SET state = 'pending', attempts = attempts - 1, lease = NULL, "
            "next_attempt_at = clock_timestamp() + make_interval(secs => 300) "
            f"WHERE event_id = :eid AND destination = :dest {_FENCE}"
        ),
        [
            {"eid": r.event_id, "dest": r.destination, "lease": lease_token}
            for r in rows
        ],
    )


def event_dict(row) -> dict:
    return {
        "event_id": str(row.event_id),
        "type": row.type,
        "aggregate": row.aggregate,
        "aggregate_id": row.aggregate_id,
        "subject_profile_id": str(row.subject_profile_id)
        if row.subject_profile_id
        else None,
        "version": row.version,
        "payload": row.payload,
    }
