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
  consumir intentos. En Fase B el worker registra al arrancar el transporte
  SOMBRA (shadow/inbox.py → jobhunt.shadow_inbox, P1-1b) SOLO si nadie
  inyectó otro: entrega real y continua sin efectos visibles (§8 del
  contrato de Fase B).
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
_FENCE = "AND d.state = 'inflight' AND d.lease = :lease"


async def mark_delivered(session, marks: list, lease_token) -> int:
    """marks = [{'eid', 'dest'}]. Solo si el claim sigue siendo NUESTRO.
    Devuelve las filas REALMENTE transicionadas (2ª rev. A-10: los contadores
    reportan lo que el fence permitió escribir, no la intención)."""
    if not marks:
        return 0
    done = (
        await session.execute(
            sa.text(
                "UPDATE integration_outbox_deliveries d "
                "SET state = 'delivered', ack_at = clock_timestamp(), lease = NULL "
                "FROM unnest(CAST(:eids AS uuid[]), CAST(:dests AS text[])) "
                "  AS t(eid, dest) "
                "WHERE d.event_id = t.eid AND d.destination = t.dest "
                f"{_FENCE} RETURNING d.event_id"
            ),
            {
                "eids": [str(m["eid"]) for m in marks],
                "dests": [m["dest"] for m in marks],
                "lease": lease_token,
            },
        )
    ).all()
    return len(done)


async def mark_failed(session, fails: list, lease_token) -> dict:
    """fails = [{'eid', 'dest', 'attempts', 'error'}] (attempts YA
    incrementado por el claim). Con fencing: un mark TARDÍO de un claim
    superado no toca nada (ni resucita delivered/dead) Y TAMPOCO alerta ni
    cuenta (2ª rev. A-10: la ALERTA de dead-letter se emite SOLO para filas
    realmente transicionadas por el UPDATE — jamás una página falsa por un
    evento que otro dispatcher sí entregó). Devuelve {'dead': n, 'retried': n}
    con transiciones REALES."""
    if not fails:
        return {"dead": 0, "retried": 0}
    dead = [f for f in fails if f["attempts"] >= MAX_ATTEMPTS]
    retry = [f for f in fails if f["attempts"] < MAX_ATTEMPTS]
    dead_done = []
    if dead:
        dead_done = (
            await session.execute(
                sa.text(
                    "UPDATE integration_outbox_deliveries d "
                    "SET state = 'dead', last_error = t.error, lease = NULL, "
                    # dead_at (core0030): instante de la TRANSICIÓN — el gate
                    # outbox_dead cuenta por ventana de ciclo, no el histórico.
                    "dead_at = clock_timestamp() "
                    "FROM unnest(CAST(:eids AS uuid[]), CAST(:dests AS text[]), "
                    "            CAST(:errors AS text[])) AS t(eid, dest, error) "
                    "WHERE d.event_id = t.eid AND d.destination = t.dest "
                    f"{_FENCE} RETURNING d.event_id, d.destination, d.attempts, "
                    "d.last_error"
                ),
                {
                    "eids": [str(f["eid"]) for f in dead],
                    "dests": [f["dest"] for f in dead],
                    "errors": [f["error"] for f in dead],
                    "lease": lease_token,
                },
            )
        ).all()
        for row in dead_done:
            # ALERTA persistente del contrato (DoD A-10), SOLO transiciones
            # reales confirmadas por el fence.
            logger.error(
                "delivery: evento %s → %s en DEAD-LETTER tras %d intentos (%s)",
                row.event_id, row.destination, row.attempts,
                (row.last_error or "")[:200],
            )
    retried = 0
    if retry:
        retried_rows = (
            await session.execute(
                sa.text(
                    "UPDATE integration_outbox_deliveries d "
                    "SET state = 'pending', last_error = t.error, lease = NULL, "
                    "ack_at = NULL, "
                    "next_attempt_at = clock_timestamp() + "
                    "make_interval(secs => t.backoff) "
                    "FROM unnest(CAST(:eids AS uuid[]), CAST(:dests AS text[]), "
                    "            CAST(:errors AS text[]), CAST(:backoffs AS int[])) "
                    "  AS t(eid, dest, error, backoff) "
                    "WHERE d.event_id = t.eid AND d.destination = t.dest "
                    f"{_FENCE} RETURNING d.event_id"
                ),
                {
                    "eids": [str(f["eid"]) for f in retry],
                    "dests": [f["dest"] for f in retry],
                    "errors": [f["error"] for f in retry],
                    "backoffs": [backoff_seconds(f["attempts"]) for f in retry],
                    "lease": lease_token,
                },
            )
        ).all()
        retried = len(retried_rows)
    return {"dead": len(dead_done), "retried": retried}


async def stats(session) -> dict:
    """Observabilidad del GATE A/B (ADR-06: monitorizar lag + dead-letter):
    conteos por estado + edad del EVENTO no entregado más viejo + dead_total.

    P2-6 (rev. externa parte 2): `oldest_pending_s` mide la EDAD DEL EVENTO
    (`clock_timestamp() − integration_outbox.created_at` del más viejo en
    pending E inflight), no la distancia a `next_attempt_at` — aquello medía
    el PRÓXIMO reintento (negativo con backoff futuro: un fallo con
    next_attempt_at en el futuro APLANABA el lag justo cuando crecía).
    Jamás negativa (GREATEST 0) y monótona mientras el evento siga sin
    entregar. El nombre de la clave se conserva (§5: fórmula actualizada en
    el contrato; los samples históricos del muestreador siguen agregables).
    `dead_total` alimenta el gate nuevo `outbox_dead` (§6)."""
    counts = {
        r.state: r.n
        for r in (
            await session.execute(
                sa.text(
                    "SELECT state, count(*) AS n "
                    "FROM integration_outbox_deliveries GROUP BY state"
                )
            )
        ).all()
    }
    oldest = (
        await session.execute(
            sa.text(
                "SELECT GREATEST(EXTRACT(EPOCH FROM clock_timestamp() - "
                "MIN(o.created_at)), 0) "
                "FROM integration_outbox_deliveries d "
                "JOIN integration_outbox o ON o.event_id = d.event_id "
                "WHERE d.state IN ('pending', 'inflight')"
            )
        )
    ).scalar_one_or_none()
    return {
        "by_state": counts,
        "oldest_pending_s": float(oldest) if oldest is not None else 0.0,
        "dead_total": int(counts.get("dead", 0)),
    }


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
