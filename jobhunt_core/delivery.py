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
# G2-H-7 (ACOTADO y OBSERVABLE desde G5 — causa raíz común de G3-P2-2,
# G4-P2-2 y G5-P2-3): el lote se transportaba con UN solo lease y sin
# renovación, así que un lote lento —el HTTP real de Fase C con 100 destinos—
# lo superaba a mitad y perdía sus marks. Ahora el dispatcher persiste el
# resultado de CADA entrega en cuanto ocurre y RENUEVA el lease del resto del
# lote antes de agotarlo (`renew_lease`, usada por tasks/delivery.py). La
# renovación es también la telemetría que faltaba (G5-P3-3):
# `lease_renewals` y `lease_overrun` en el resumen del ciclo, contados en
# ORIGEN.
# NO está CERRADO (G6-N-2): la renovación se evalúa ENTRE elementos, así que
# un SOLO elemento cuyo transporte tarde más que LEASE_S —el timeout HTTP
# colgado de Fase C— pierde el lote entero antes de la primera oportunidad de
# renovar. Lo que hay es una cota (la ventana es un elemento, no el lote) y un
# rastro veraz, no la desaparición del modo de fallo.
LEASE_RENEW_AFTER_S = LEASE_S / 2  # renovar a mitad de vida, no al filo

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
    A-10), un timestamp calculado en BD. G5-N-5 — qué protege HOY, que no es
    lo que este docstring afirmaba: el fence condiciona el FALLO
    (`mark_failed`), no el ÉXITO; una entrega confirmada es un hecho del
    transporte y se persiste con la guarda de estado no-terminal. Ni una vía
    ni la otra resucitan un `delivered`/`dead`. El token no es fijo para todo
    el lote: `renew_lease` lo renueva mientras el dispatcher siga trabajando
    (G2-H-7) y el token nuevo describe las filas que aún poseemos."""
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
                # Orden TOTAL y determinista (G5-P2-2): con el desempate por
                # (event_id, destination) la CABEZA de la cola es la misma en
                # cada vuelta y la comparte `retire_poisoned`, que retira
                # exactamente la fila que el dispatcher intenta transportar
                # primero — el veneno, por construcción.
                "ORDER BY d.next_attempt_at ASC NULLS FIRST, "
                "         d.event_id, d.destination "
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


async def renew_lease(session, rows, lease_token) -> tuple[object, int]:
    """Renueva el lease de lo que QUEDA del lote y devuelve (token_nuevo,
    perdidas) — G2-H-7.

    El transporte corre fuera de la transacción del claim, así que un lote
    lento supera el lease a mitad y el resto del lote se lo re-clama otro
    dispatcher: re-entrega (legal, el inbox deduplica) y, sobre todo, marks
    descartados. Renovando a mitad de vida el dueño conserva su claim mientras
    siga trabajando. Solo se renuevan las filas que TODAVÍA son nuestras
    ('inflight' con NUESTRO lease); las que ya no lo son se cuentan como
    `perdidas` y se registran — es la señal de desbordamiento contada en
    ORIGEN que G5-P3-3 echaba en falta. El token nuevo describe exactamente
    las filas que seguimos poseyendo, así que el fence sigue siendo exacto."""
    if not rows:
        return lease_token, 0
    nuevo = (
        await session.execute(
            sa.text(
                "SELECT clock_timestamp() + "
                f"make_interval(secs => {int(LEASE_S)})"
            )
        )
    ).scalar_one()
    kept = (
        await session.execute(
            sa.text(
                "UPDATE integration_outbox_deliveries d SET lease = :nuevo "
                "FROM unnest(CAST(:eids AS uuid[]), CAST(:dests AS text[])) "
                "  AS t(eid, dest) "
                "WHERE d.event_id = t.eid AND d.destination = t.dest "
                "AND d.state = 'inflight' AND d.lease = :old "
                "RETURNING d.event_id"
            ),
            {
                "nuevo": nuevo, "old": lease_token,
                "eids": [str(r.event_id) for r in rows],
                "dests": [r.destination for r in rows],
            },
        )
    ).all()
    perdidas = len(rows) - len(kept)
    if perdidas:
        logger.warning(
            "delivery: el lote superó el lease — %d de %d entregas pendientes "
            "ya no son nuestras (otro dispatcher las re-clamó): re-entrega "
            "at-least-once en curso",
            perdidas, len(rows),
        )
    return nuevo, perdidas


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
    (lease caducado). UNA sola por ciclo, la CABEZA de los candidatos.

    G5-P2-2: el radio de explosión estaba sin acotar. Con el dispatcher
    persistiendo los resultados al final del lote entero, un payload que MATA
    al proceso hacía que NINGÚN mark del lote se ejecutara, así que sus hasta
    99 vecinos —transportados CORRECTAMENTE en cada vuelta— cruzaban el umbral
    a la vez y se retiraban todos como veneno, con un `last_error` que afirma
    lo contrario de lo ocurrido. La otra mitad del cierre está en
    tasks/delivery.py (resultado persistido por entrega, que salva a los
    vecinos 1..K-1 de un veneno en posición K > 0). Aquí: LIMIT 1 con el MISMO
    orden total que `claim_deliveries`, así que la fila retirada es la CABEZA
    de los CANDIDATOS a veneno — el veneno por construcción, no un vecino
    sano. G7-N-2: «candidatos a veneno», no «lo que el dispatcher transporta
    primero»: el claim admite además `pending` con `next_attempt_at` vencido,
    que este filtro ni ve, así que la cabeza del claim puede ser otra fila.
    G6-N-1: en el caso base (veneno en la cabeza) es ESTE LIMIT 1
    el que acota el radio, no la persistencia por entrega: detrás del veneno
    nadie llega a transportarse, así que los 99 de atrás acumulan reclamos
    igual que antes del fix y lo único que impide la matanza colateral es que
    solo se retire UNA fila por ciclo.

    G6-P2-1: el LIMIT 1 elige la CABEZA; la ELEGIBILIDAD la decide el WHERE del
    UPDATE. Al mover `state/lease/claims` DENTRO del subplan se perdió la
    guarda de estado del propio UPDATE: el subplan es uncorrelated, Postgres lo
    resuelve como InitPlan una sola vez por sentencia y el recheck EPQ (cuando
    el UPDATE se desbloquea tras esperar el lock de otra transacción) re-evalúa
    los quals con la fila NUEVA pero NO vuelve a ejecutar el InitPlan. Con la
    igualdad contra una constante como único qual, la fila se pisaba fuera cual
    fuera su estado nuevo: una entrega CONFIRMADA (`delivered`, con `ack_at`)
    acababa `dead` con un `last_error` de veneno, y un reintento `pending` con
    intentos por delante moría TERMINAL. Esa misma guarda es la que cierra la
    doble retirada de la MISMA fila por dos dispatchers (dos `poisoned`, dos
    alertas y `dead_at` reescrito, que es lo que ventana el gate
    `outbox_dead`): el segundo UPDATE la encuentra ya `dead` y no la toca.

    G7-P2-1: el `FOR UPDATE SKIP LOCKED` que G6 añadió al subplan para cerrar
    esa doble retirada ANULABA el LIMIT 1 —ver el comentario del propio
    subplan— y devolvía la matanza colateral que G5-P2-2 había cerrado. Se
    retiró: la elegibilidad la decide el WHERE del UPDATE, y el subplan solo
    elige la CABEZA. Arista conocida (no reproducida): al volver a bloquear,
    este UPDATE puede esperar el lock de otro dispatcher mientras retiene los
    que `retire_exhausted` tomó justo antes en el mismo ciclo (mismo orden de
    llegada en `tasks/delivery.py`, pero `retire_exhausted` no impone orden
    explícito) ⇒ deadlock teórico entre dos ciclos solapados; Postgres lo
    detecta y aborta uno, y `dispatch_outbox_task` reintenta.

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
                # G6-P2-1: la elegibilidad va en el WHERE del UPDATE, NO solo
                # dentro del subplan. El subplan es uncorrelated ⇒ Postgres lo
                # ejecuta como InitPlan UNA vez por sentencia y NO lo re-evalúa
                # en el recheck EPQ; si las tres condiciones viven solo ahí, lo
                # único que queda al desbloquearse el lock es la igualdad
                # contra una constante ya calculada, que se cumple SIEMPRE.
                "WHERE d.state = 'inflight' AND d.lease < clock_timestamp() "
                "AND d.claims >= :max "
                "AND (d.event_id, d.destination) = ("
                "  SELECT x.event_id, x.destination "
                "  FROM integration_outbox_deliveries x "
                "  WHERE x.state = 'inflight' AND x.lease < clock_timestamp() "
                "  AND x.claims >= :max "
                "  ORDER BY x.next_attempt_at ASC NULLS FIRST, "
                "           x.event_id, x.destination "
                # G7-P2-1: el subplan NO lleva cláusula de bloqueo. `SKIP
                # LOCKED` no evita bloquear: ELIGE OTRA FILA. Con la cabeza
                # bloqueada —el caso NORMAL: `claim_deliveries` la bloquea con
                # su propio FOR UPDATE SKIP LOCKED— el subplan la saltaba y el
                # UPDATE se ejecutaba sobre el VECINO: un evento con
                # `attempts = 0` que jamás se transportó moría en dead-letter
                # mientras el veneno REAL sobrevivía, y dos dispatchers
                # solapados retiraban DOS filas por ciclo. Es la matanza
                # colateral de G5-P2-2, reabierta. `FOR UPDATE` a secas
                # TAMPOCO vale: el LockRows espera el lock, re-evalúa los
                # quals del subplan sobre la fila nueva y, si la cabeza dejó
                # de ser elegible, la filtra y el LIMIT 1 toma al mismo vecino
                # por otra puerta. Sin cláusula de bloqueo el subplan elige
                # SIEMPRE la cabeza y quien decide es el recheck EPQ del
                # propio UPDATE —que sí re-evalúa state/lease/claims
                # (G6-P2-1)—: si otro dispatcher se la re-clamó no se toca
                # nada; si sigue siendo veneno, se retira UNA. Esa misma
                # guarda cierra ya la doble retirada que el SKIP LOCKED decía
                # comprar. Coste: el UPDATE puede esperar un lock breve, que
                # es exactamente lo que se quiere aquí.
                "  LIMIT 1) "
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


async def mark_delivered(session, marks: list, lease_token=None) -> int:
    """marks = [{'eid', 'dest'}]. La entrega CONFIRMADA se persiste aunque el
    lease ya no sea nuestro. Devuelve las filas REALMENTE transicionadas.

    `lease_token` NO condiciona la escritura (G5-N-5: era un parámetro muerto
    desde que el éxito salió del fence). Se conserva —opcional— porque los
    llamadores lo tienen y porque documenta de qué claim viene el mark; el
    contrato que importa es el de la guarda de estado, abajo.

    G4-P2-2: G3-P2-2 sacó del fence la persistencia del resultado SOLO para el
    FALLO. El camino de ÉXITO seguía entero detrás del fence, y es el único
    sitio donde `claims` vuelve a 0 tras una entrega buena: un transporte que
    ENTREGA BIEN pero supera el lease (G2-H-7 — un lote de 100 entregas HTTP
    de Fase C lo supera de sobra) veía TODOS sus marks descartados, `attempts`
    se quedaba en 0 (fuera del alcance de `retire_exhausted`) y `claims` crecía
    hasta 25 ⇒ `retire_poisoned` mataba como VENENO un evento entregado
    correctamente 25 veces, con un `last_error` que afirma lo contrario de lo
    ocurrido. El detector medía «reclamos sin MARK» y no «reclamos sin
    RESULTADO»: dos cosas distintas en cuanto el fence se interpone.

    G6-P2-2: el `robada` del RETURNING leía la fila NUEVA y la propia SET ya
    había puesto `lease = NULL`, así que la comparación era TRUE SIEMPRE y el
    WARNING «G2-H-7 en vivo» —el único rastro que sustituye a `fenced_out`
    (G5-P3-3)— se emitía en el 100 % de las entregas SANAS. Un operador que lo
    siguiera concluiría que G2-H-7 está en vivo de forma permanente y, en
    cuanto aprendiera a ignorarlo, el caso REAL pasaría inadvertido.

    G7-P2-2: el self-join `prev` con el que G6 lo arregló leía el SNAPSHOT de
    la sentencia, SIN lock, y eso lo rompía en la otra dirección. En READ
    COMMITTED el snapshot se toma al empezar la sentencia: el UPDATE espera el
    lock del ladrón y, al desbloquearse, hace EPQ sobre la fila NUEVA — pero
    el escaneo de `prev` NO participa del EPQ y sigue devolviendo la versión
    ANTERIOR al commit del ladrón. `prev.lease` valía todavía el NUESTRO y el
    aviso NO sonaba justo en el interleaving REAL de G2-H-7 (el re-claim de B
    commitea MIENTRAS el UPDATE de A espera): falso positivo permanente
    cambiado por falso negativo en el único caso que la señal existe para
    delatar. Ahora el lease previo se lee CON LOCK en un pre-SELECT
    `FOR UPDATE` (una fila: el dispatcher marca de una en una,
    tasks/delivery.py), que devuelve la versión más reciente COMMITEADA y de
    paso deja al UPDATE sin EPQ que hacer.

    G6-N-3, escrito para que nadie lo lea como un bug: `attempts` se incrementa
    también cuando la fila llegó a `pending` porque el `mark_failed` de OTRO
    dispatcher ya consumió su intento. Son DOS transportes REALMENTE
    ejecutados (el nuestro, que entregó, y el suyo, que falló) ⇒ dos intentos:
    el contador mide ejecuciones del transporte, que es la garantía de
    G2-P3-4, y no «vueltas de la fila».

    Por qué el lease NO condiciona esta escritura: una entrega confirmada es un
    HECHO del transporte, independiente de quién posea el claim. Lo que el
    fence protege de verdad es no resucitar un estado TERMINAL, y eso lo da la
    guarda `state IN ('pending','inflight')`: `delivered`/`dead` siguen
    intocables. Cierra además la RE-ENTREGA INFINITA: antes la fila volvía a
    `pending` cada ciclo sin avanzar nada. `attempts` sigue consumiéndose SOLO
    en el resultado (G2-P3-4 intacto).

    G5-P2-3: la guarda NO podía ser `state = 'inflight'`. Protegía menos de lo
    que el comentario prometía: fallaba también cuando otro dispatcher ya
    había RESUELTO la fila. Bajo la condición G2-H-7, A supera el lease
    entregando BIEN, B se re-clama la fila y su transporte da timeout; el
    `mark_failed` de B (dueño legítimo) manda la fila a `pending` y consume el
    intento, y el éxito CONFIRMADO de A se descartaba en silencio. Repetido,
    el evento moría por MAX_ATTEMPTS con un `last_error` que afirma lo
    contrario de lo ocurrido — 8 entregas reales al inbox y estado final
    `dead`. Una entrega confirmada gana sobre un reintento programado, así que
    también se limpian `next_attempt_at` y `last_error` (G5-N-5: una fila que
    falló y luego se entregó quedaba `delivered` con el error anterior)."""
    if not marks:
        return 0
    eids = [str(m["eid"]) for m in marks]
    dests = [m["dest"] for m in marks]
    # G7-P2-2: la foto PREVIA de la fila, leída CON LOCK. Bloquea hasta que
    # el eventual ladrón commitea, así que el lease que vuelve es el vigente
    # de VERDAD y no el del snapshot. `ORDER BY` para no introducir orden de
    # lock nuevo (el mismo del claim). Es de solo lectura: no transiciona
    # nada, y las filas que va a tocar el UPDATE de abajo son exactamente
    # éstas.
    previos = {
        (r.event_id, r.destination): r.lease
        for r in (
            await session.execute(
                sa.text(
                    "SELECT d.event_id, d.destination, d.lease "
                    "FROM integration_outbox_deliveries d "
                    "WHERE (d.event_id, d.destination) IN ("
                    "  SELECT t.eid, t.dest FROM unnest("
                    "    CAST(:eids AS uuid[]), CAST(:dests AS text[])"
                    "  ) AS t(eid, dest)) "
                    "ORDER BY d.event_id, d.destination "
                    "FOR UPDATE"
                ),
                {"eids": eids, "dests": dests},
            )
        ).all()
    }
    done = (
        await session.execute(
            sa.text(
                "UPDATE integration_outbox_deliveries d "
                "SET state = 'delivered', ack_at = clock_timestamp(), lease = NULL, "
                # G2-P3-4: el intento se consume al EJECUTAR el transporte.
                # G3-H-1: y el contador de veneno se limpia con el resultado.
                "attempts = d.attempts + 1, claims = 0, "
                # G5-P2-3/G5-N-5: la entrega gana sobre el reintento que otro
                # dispatcher hubiera programado, y no deja un error que ya no
                # describe el estado de la fila.
                "next_attempt_at = NULL, last_error = NULL "
                "FROM unnest(CAST(:eids AS uuid[]), CAST(:dests AS text[])) "
                "  AS t(eid, dest) "
                "WHERE d.event_id = t.eid AND d.destination = t.dest "
                "AND d.state IN ('pending', 'inflight') "
                # El `robada` NO puede salir de aquí: el RETURNING de un
                # UPDATE se evalúa sobre la fila NUEVA y esta misma SET pone
                # `lease = NULL` (G6-P2-2). Sale del pre-SELECT bloqueante.
                "RETURNING d.event_id, d.destination"
            ),
            {"eids": eids, "dests": dests},
        )
    ).all()
    # G5-P3-3: el desbordamiento del lease contado en ORIGEN — el mark aterrizó
    # sobre una fila cuyo lease ya no era el nuestro (otro dispatcher la
    # re-clamó, o su mark_failed la devolvió a 'pending' con el lease a NULL).
    # Solo se cuentan filas REALMENTE transicionadas por el UPDATE.
    robadas = sum(
        1
        for r in done
        if previos.get((r.event_id, r.destination)) != lease_token
    )
    if robadas and lease_token is not None:
        logger.warning(
            "delivery: %d entrega(s) CONFIRMADAS marcadas sobre filas cuyo "
            "lease ya no era nuestro — el lote superó LEASE_S y otro "
            "dispatcher re-clamó (re-entrega at-least-once, el inbox "
            "deduplica): G2-H-7 en vivo",
            robadas,
        )
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
