"""Entrega at-least-once del outbox POR destino (A-10, ADR-06).

- La EMISIÓN va en la misma transacción que la escritura (matching, ADR-05);
  aquí vive el DESPACHO: claim con `FOR UPDATE SKIP LOCKED` + lease (un lease
  caducado se re-reclama: at-least-once real), backoff exponencial por
  intento, DEAD-LETTER con ALERTA al agotar los intentos — y, por una vía
  SEPARADA (`claims`, G3-H-1), al agotar los RECLAMOS sin haber producido
  jamás un resultado: el veneno que tumba al dispatcher no consume intentos y
  bloquearía la cabeza de la cola para siempre.
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
# G3-H-1: tope de RECLAMOS CONSECUTIVOS SIN resultado (`claims`, core0032) —
# el detector de VENENO: un payload que MATA al proceso del dispatcher (OOM,
# segfault del driver) nunca llega a marcar, así que no consume `attempts` y
# el dead-letter por agotamiento no puede alcanzarlo; y con
# `ORDER BY next_attempt_at NULLS FIRST` ocupa la CABEZA de la cola. 25 con la
# cadencia del beat (CORE_DELIVERY_DISPATCH_EVERY_S = 5 min) son ~2 h de
# crash-loop ININTERRUMPIDO sobre el MISMO mensaje sin un solo resultado: un
# redespliegue en bucle o un OOM puntual —el escenario que G2-P3-4 protege— no
# llega ahí, y como triplica MAX_ATTEMPTS un destino simplemente CAÍDO siempre
# muere antes por la vía normal (que sí consumió intentos reales).
MAX_CLAIMS_WITHOUT_RESULT = 25
BACKOFF_BASE_S = 60
BACKOFF_CAP_S = 3600
LEASE_S = 120
# G2-H-7 (registrado para el cutover de Fase C): UN solo lease para el lote
# entero, sin renovación. Irrelevante con el transporte sombra (INSERT local),
# pero con el HTTP real un lote lento puede superar el lease a mitad → re-claim
# y re-entrega de la cola del lote (at-least-once legal, el inbox deduplica) con
# los marks tardíos `fenced_out`. Si aquello se vuelve visible, renovar el lease
# por sub-lote; el consumo de intentos ya no lo amplifica (G2-P3-4).

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
    SKIP LOCKED: varios dispatchers no se pisan. Toma el lease; `attempts`
    NO se toca aquí (G2-P3-4): lo consume el RESULTADO del transporte, así
    que un re-claim por lease caducado no gasta intentos.

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
            # G2-P3-4: el claim NO consume intento — lo consume el RESULTADO
            # (mark_delivered/mark_failed). Un dispatcher que muere entre el
            # claim commiteado y los marks (OOM, redeploy en bucle) devolvía el
            # evento por lease caducado y cada re-claim quemaba un intento SIN
            # que el transporte hubiera corrido jamás: tras 7 claims fantasma,
            # el PRIMER fallo real llegaba con attempts=8 ⇒ dead-letter con una
            # única ejecución real. El módulo ya enunciaba el principio
            # contrario para el caso sin-transporte.
            # G3-H-1: `claims` SÍ se toca aquí (no `attempts`): mide reclamos
            # CONSECUTIVOS sin resultado y lo pone a 0 el primer mark, así que
            # solo crece cuando el transporte no llega NUNCA a completar.
            "UPDATE integration_outbox_deliveries "
            "SET state = 'inflight', lease = :lease, claims = claims + 1 "
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


async def _persist_attempts(session, fails: list) -> None:
    """Persiste el intento EJECUTADO y su error FUERA del fence del lease.

    G3-P2-2: al mover el consumo de `attempts` del claim al RESULTADO
    (G2-P3-4), los dos escritores quedaron DETRÁS del fence. Un lote cuyo
    transporte supera el lease (G2-H-7) es re-reclamado por el siguiente beat
    y TODOS sus marks se descartan: no se persistía el intento NI el
    `last_error`, así que con fallos reales y repetidos el contador nunca
    avanzaba y el DEAD-LETTER (DoD A-10) era inalcanzable — reintento infinito
    y mudo contra un endpoint colgado, justo el caso para el que existe.

    El fence protege la TRANSICIÓN DE ESTADO (no resucitar un terminal, no
    pisar al nuevo dueño); el contador es MONÓTONO y cuenta transportes
    REALMENTE ejecutados, así que se escribe sin lease — pero solo sobre filas
    aún 'inflight': una delivered/dead ajena jamás se toca."""
    if not fails:
        return
    await session.execute(
        sa.text(
            "UPDATE integration_outbox_deliveries d "
            "SET attempts = GREATEST(d.attempts, t.attempts), "
            "    last_error = t.error, "
            # G3-H-1: hubo RESULTADO (el transporte se ejecutó y falló) — el
            # contador de veneno vuelve a 0 aunque el fence descarte la
            # transición: un destino caído jamás se confunde con un payload
            # que mata al proceso.
            "    claims = 0 "
            "FROM unnest(CAST(:eids AS uuid[]), CAST(:dests AS text[]), "
            "            CAST(:errors AS text[]), CAST(:tries AS int[])) "
            "  AS t(eid, dest, error, attempts) "
            "WHERE d.event_id = t.eid AND d.destination = t.dest "
            "AND d.state = 'inflight'"
        ),
        {
            "eids": [str(f["eid"]) for f in fails],
            "dests": [f["dest"] for f in fails],
            "errors": [f["error"] for f in fails],
            "tries": [int(f["attempts"]) for f in fails],
        },
    )


async def retire_exhausted(session) -> int:
    """DEAD-LETTER de rescate: entregas 'inflight' con el lease CADUCADO
    (nadie las posee) cuyos intentos REALES ya se agotaron.

    G3-P2-2 (2ª mitad): con el transporte superando el lease en cada ciclo,
    el mark del dueño superado SIEMPRE cae por el fence, así que la transición
    a 'dead' no llegaba a escribirse ni con `attempts` ya en MAX_ATTEMPTS.
    Los intentos que se cuentan aquí son transportes EJECUTADOS
    (_persist_attempts, monótono), nunca claims fantasma: la garantía de
    G2-P3-4 —no gastar intentos sin transporte— se conserva intacta.
    Emite la MISMA alerta persistente del contrato que mark_failed."""
    rows = (
        await session.execute(
            sa.text(
                "UPDATE integration_outbox_deliveries d "
                "SET state = 'dead', lease = NULL, dead_at = clock_timestamp() "
                "WHERE d.state = 'inflight' AND d.lease < clock_timestamp() "
                "AND d.attempts >= :max "
                "RETURNING d.event_id, d.destination, d.attempts, d.last_error"
            ),
            {"max": MAX_ATTEMPTS},
        )
    ).all()
    for row in rows:
        logger.error(
            "delivery: evento %s → %s en DEAD-LETTER tras %d intentos (%s) "
            "— lease caducado sin mark: retirado al re-reclamar",
            row.event_id, row.destination, row.attempts,
            (row.last_error or "")[:200],
        )
    return len(rows)


async def retire_poisoned(session) -> int:
    """DEAD-LETTER por VENENO: entregas reclamadas MAX_CLAIMS_WITHOUT_RESULT
    veces seguidas sin producir JAMÁS un resultado, y que ahora no posee nadie
    (lease caducado).

    G3-H-1: es el modo de fallo que abrió G2-P3-4 al sacar el consumo de
    intentos del claim — un payload que mata al proceso del dispatcher antes
    de cualquier mark reintenta para siempre con `attempts = 0` y, por el
    `ORDER BY next_attempt_at NULLS FIRST`, bloquea la CABEZA de la cola. No se
    toca `attempts` (G2-P3-4 sigue intacto: nadie gasta intentos sin
    transporte); se retira por un contador PROPIO y con una razón que
    distingue el veneno del agotamiento normal — el operador necesita saber si
    el problema es el MENSAJE o el DESTINO."""
    rows = (
        await session.execute(
            sa.text(
                "UPDATE integration_outbox_deliveries d "
                "SET state = 'dead', lease = NULL, dead_at = clock_timestamp(), "
                "    last_error = :reason "
                "WHERE d.state = 'inflight' AND d.lease < clock_timestamp() "
                "AND d.claims >= :max "
                "RETURNING d.event_id, d.destination, d.claims, d.attempts"
            ),
            {
                "max": MAX_CLAIMS_WITHOUT_RESULT,
                "reason": (
                    f"veneno: {MAX_CLAIMS_WITHOUT_RESULT} reclamos consecutivos "
                    "sin un solo resultado del transporte (el dispatcher nunca "
                    "llegó a marcar) — NO es un destino caído"
                ),
            },
        )
    ).all()
    for row in rows:
        logger.error(
            "delivery: evento %s → %s en DEAD-LETTER por VENENO tras %d "
            "reclamos consecutivos SIN un solo resultado (intentos "
            "consumidos: %d): el payload tumba al dispatcher — revisar el "
            "MENSAJE, no el destino",
            row.event_id, row.destination, row.claims, row.attempts,
        )
    return len(rows)


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
                "SET state = 'delivered', ack_at = clock_timestamp(), lease = NULL, "
                # G2-P3-4: el intento se consume al EJECUTAR el transporte.
                # G3-H-1: y el contador de veneno se limpia con el resultado.
                "attempts = d.attempts + 1, claims = 0 "
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
    """fails = [{'eid', 'dest', 'attempts', 'error'}] — `attempts` es el NÚMERO
    del intento que el dispatcher ACABA de ejecutar (r.attempts + 1), y este
    mark es quien lo persiste (G2-P3-4: el claim ya no lo consume). Con fencing: un mark TARDÍO de un claim
    superado no toca nada (ni resucita delivered/dead) Y TAMPOCO alerta ni
    cuenta (2ª rev. A-10: la ALERTA de dead-letter se emite SOLO para filas
    realmente transicionadas por el UPDATE — jamás una página falsa por un
    evento que otro dispatcher sí entregó). Devuelve {'dead': n, 'retried': n}
    con transiciones REALES."""
    if not fails:
        return {"dead": 0, "retried": 0}
    # G3-P2-2: el intento y el error se persisten ANTES y FUERA del fence —
    # un mark descartado por el fence perdía ambos y el dead-letter no se
    # alcanzaba jamás. Los UPDATE fenceados de abajo reescriben el MISMO
    # valor cuando el claim sigue siendo nuestro.
    await _persist_attempts(session, fails)
    dead = [f for f in fails if f["attempts"] >= MAX_ATTEMPTS]
    retry = [f for f in fails if f["attempts"] < MAX_ATTEMPTS]
    dead_done = []
    if dead:
        dead_done = (
            await session.execute(
                sa.text(
                    "UPDATE integration_outbox_deliveries d "
                    "SET state = 'dead', last_error = t.error, lease = NULL, "
                    # G2-P3-4: `attempts` lo fija el RESULTADO (el nº de intento
                    # que el dispatcher acaba de ejecutar), no el claim; G3-P2-2:
                    # MONÓTONO (nunca por debajo de lo ya persistido).
                    "attempts = GREATEST(d.attempts, t.attempts), "
                    # dead_at (core0030): instante de la TRANSICIÓN — el gate
                    # outbox_dead cuenta por ventana de ciclo, no el histórico.
                    "dead_at = clock_timestamp() "
                    "FROM unnest(CAST(:eids AS uuid[]), CAST(:dests AS text[]), "
                    "            CAST(:errors AS text[]), CAST(:tries AS int[])) "
                    "  AS t(eid, dest, error, attempts) "
                    "WHERE d.event_id = t.eid AND d.destination = t.dest "
                    f"{_FENCE} RETURNING d.event_id, d.destination, d.attempts, "
                    "d.last_error"
                ),
                {
                    "eids": [str(f["eid"]) for f in dead],
                    "dests": [f["dest"] for f in dead],
                    "errors": [f["error"] for f in dead],
                    "tries": [int(f["attempts"]) for f in dead],
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
                    "ack_at = NULL, attempts = GREATEST(d.attempts, t.attempts), "
                    "next_attempt_at = clock_timestamp() + "
                    "make_interval(secs => t.backoff) "
                    "FROM unnest(CAST(:eids AS uuid[]), CAST(:dests AS text[]), "
                    "            CAST(:errors AS text[]), CAST(:backoffs AS int[]), "
                    "            CAST(:tries AS int[])) "
                    "  AS t(eid, dest, error, backoff, attempts) "
                    "WHERE d.event_id = t.eid AND d.destination = t.dest "
                    f"{_FENCE} RETURNING d.event_id"
                ),
                {
                    "eids": [str(f["eid"]) for f in retry],
                    "dests": [f["dest"] for f in retry],
                    "errors": [f["error"] for f in retry],
                    "backoffs": [backoff_seconds(f["attempts"]) for f in retry],
                    "tries": [int(f["attempts"]) for f in retry],
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
