"""Idempotency-Key para las escrituras del /v1 (C-API-W, PLAN §15bis/§4).

Header OPCIONAL `Idempotency-Key`. La PK NATURAL de `idempotency_records`
—(consumer_id, key, route)— ES el candado anti-repetición; `route` lleva
método + path concreto (p.ej. "PUT /v1/profiles/<uuid>") ⇒ la misma key sobre
recursos/verbos distintos son reservas INDEPENDIENTES.

Mecanismo — UNA sola transacción (atomicidad reserva+efecto+respuesta):

1. INSERT de la reserva (response NULL) `ON CONFLICT DO NOTHING RETURNING`.
2. Si la reserva es NUESTRA (RETURNING trajo fila): ejecutamos el handler EN
   ESTA transacción, guardamos {status, body} en la fila y COMMIT — reserva,
   efecto y respuesta caen JUNTOS o no caen. Un fallo del handler propaga y el
   cierre de sesión hace rollback: la key queda LIBRE (un crash JAMÁS envenena
   una key, a diferencia del patrón de dos commits del que hablaría un
   response=NULL persistido).
3. Si hay CONFLICTO: Postgres BLOQUEA nuestro INSERT hasta que la transacción
   dueña termina — pero la espera está DOBLEMENTE acotada (P1 rev. externa):
   por la duración del handler Y por `lock_timeout` local
   (CORE_IDEMPOTENCY_LOCK_TIMEOUT_MS). Si el dueño se cuelga y se vence el
   lock_timeout, el INSERT aborta con 55P03 ⇒ rollback + 409
   idempotency_in_progress (un reintento del cliente, jamás doble ejecución ni
   un worker bloqueado sine die). Si el dueño termina a tiempo, su fila ya está
   COMMITEADA y el reintento la replica:
   - request_hash DISTINTO ⇒ 409 `idempotency_conflict` (la key se reusó para
     otro cuerpo).
   - request_hash IGUAL ⇒ devolvemos la respuesta guardada SIN re-ejecutar.
   Como no se commitea NUNCA una reserva "desnuda", toda fila VISIBLE trae
   response; un response=NULL en conflicto es inalcanzable por construcción y
   se trata, defensivamente, como en-vuelo ⇒ 409 (jamás doble ejecución).

request_hash lo calcula el llamador sobre el cuerpo canónico (mismo endpoint,
misma key, distinto cuerpo ⇒ 409); `route` ya distingue verbo y recurso.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from jobhunt_core.api.deps import ApiError, ensure_text_storable
from jobhunt_core.config import settings

logger = logging.getLogger(__name__)

# TTL de la reserva: cota para la purga (no para la validez de la respuesta —
# mientras la fila viva, un reintento la replica). 24h cubre de sobra un
# reintento de red del BFF.
IDEM_TTL = timedelta(hours=24)
KEY_MAX_LEN = 200  # = longitud de idempotency_records.key
# SQLSTATE 55P03 = lock_not_available: lo emite Postgres al vencer lock_timeout.
_LOCK_NOT_AVAILABLE = "55P03"


def _is_lock_timeout(exc: DBAPIError) -> bool:
    """True si el error es un vencimiento de lock_timeout (55P03). Cubre psycopg2
    (`pgcode`) y psycopg3/asyncpg (`sqlstate`) sin acoplar al driver."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
    return code == _LOCK_NOT_AVAILABLE


async def _reserve_or_read(session, keys, request_hash, expires):
    """(owned, row): reserva la key con INSERT ... ON CONFLICT DO NOTHING y, si
    conflictó, LEE la fila del dueño. `row` es None cuando la fila que provocó
    el conflicto ya no existe (purga concurrente — G2-P3-3): el llamador decide
    si reintentar. Ambas sentencias son las del contrato; el `.one_or_none()`
    sustituye al `.one()` que reventaba en esa carrera."""
    owned = (
        await session.execute(
            sa.text(
                "INSERT INTO idempotency_records "
                "(consumer_id, key, route, request_hash, response, expires_at) "
                "VALUES (:cid, :k, :r, :h, NULL, :exp) "
                "ON CONFLICT (consumer_id, key, route) DO NOTHING "
                "RETURNING consumer_id"
            ),
            {**keys, "h": request_hash, "exp": expires},
        )
    ).scalar_one_or_none()
    if owned is not None:
        return owned, None
    row = (
        await session.execute(
            sa.text(
                "SELECT request_hash, response FROM idempotency_records "
                "WHERE consumer_id = :cid AND key = :k AND route = :r"
            ),
            keys,
        )
    ).one_or_none()
    return None, row


async def run_idempotent(session, principal, route, request_hash, key, handler):
    """Ejecuta `handler` con idempotencia si `key` no es None.

    handler: coroutine SIN args que hace la escritura EN `session` y devuelve
    (status_code:int, payload:dict). run_idempotent es dueño del COMMIT.
    """
    if key is None:
        status, payload = await handler()
        await session.commit()
        return status, payload

    key = key.strip()
    if not key or len(key) > KEY_MAX_LEN:
        raise ApiError(
            400, "invalid_idempotency_key",
            f"Idempotency-Key vacía o mayor de {KEY_MAX_LEN}",
        )
    # G8-N-4: la cabecera va CRUDA a `idempotency_records.key` (columna
    # `text`), que es el mismo sink que el resto del cuerpo — misma clase,
    # misma regla, con el código de error que esta frontera ya tiene. Lo que
    # hoy para un NUL ahí es el parser HTTP (`h11` responde 400 antes del
    # ASGI porque la imagen no lleva `httptools`), y eso es una propiedad de
    # la IMAGEN, no del código: si un día entra httptools, el 500 aparece sin
    # que nadie toque una línea.
    ensure_text_storable(key, "Idempotency-Key", code="invalid_idempotency_key")
    expires = datetime.now(timezone.utc) + IDEM_TTL
    keys = {"cid": principal.consumer_id, "k": key, "r": route}

    # ACOTA la espera del INSERT-reserva sobre el índice único (P1 rev. externa):
    # `set_config(..., is_local=true)` fija lock_timeout SOLO para esta
    # transacción. Si otro dueño retiene la reserva más de lo permitido, el
    # INSERT aborta con 55P03 en vez de bloquear indefinidamente.
    await session.execute(
        sa.text("SELECT set_config('lock_timeout', :ms, true)"),
        {"ms": str(settings.CORE_IDEMPOTENCY_LOCK_TIMEOUT_MS)},
    )
    try:
        owned, row = await _reserve_or_read(session, keys, request_hash, expires)
        if owned is None and row is None:
            # G2-P3-3: la reserva conflictó pero la fila YA NO ESTÁ — la purga
            # (beat horario) borró la caducada y commiteó entre el INSERT (que
            # con DO NOTHING no bloquea la fila existente) y el SELECT (READ
            # COMMITTED: snapshot nuevo por sentencia). La key quedó LIBRE, así
            # que se reintenta la reserva UNA vez: ejecución normal en vez de un
            # NoResultFound → 500.
            owned, row = await _reserve_or_read(session, keys, request_hash, expires)
    except DBAPIError as exc:
        # asyncpg envuelve el 55P03 como DBAPIError (no siempre OperationalError):
        # se captura la base y se filtra por sqlstate. Cualquier otro error de BD
        # se re-lanza intacto.
        if not _is_lock_timeout(exc):
            raise
        # El dueño sigue en curso más allá del lock_timeout: la transacción
        # queda abortada ⇒ rollback y 409 en-vuelo (un reintible legítimo del
        # cliente, no una doble ejecución). Libera el worker de inmediato.
        await session.rollback()
        raise ApiError(
            409, "idempotency_in_progress",
            "otra petición con la misma Idempotency-Key sigue en curso "
            "(espera de reserva agotada)",
            {"key": key},
        ) from exc

    if owned is not None:
        # La cota de espera protegía SOLO la reserva; el handler corre con la
        # semántica de bloqueo por defecto (0 = sin límite) para no alterar su
        # comportamiento previo ante contención legítima de sus propias filas.
        await session.execute(sa.text("SELECT set_config('lock_timeout', '0', true)"))
        # Reserva NUESTRA: ejecuta y persiste la respuesta en el MISMO commit.
        status, payload = await handler()
        await session.execute(
            sa.text(
                "UPDATE idempotency_records SET response = CAST(:resp AS jsonb) "
                "WHERE consumer_id = :cid AND key = :k AND route = :r"
            ),
            {**keys, "resp": json.dumps({"status": status, "body": payload})},
        )
        await session.commit()
        return status, payload

    # CONFLICTO: nuestro INSERT esperó al dueño; su fila ya está commiteada.
    if row is None:
        # Dos carreras seguidas con la purga (ventana de milisegundos): 409
        # reintentable, jamás un 500 ni una doble ejecución.
        raise ApiError(
            409, "idempotency_in_progress",
            "la reserva de esta Idempotency-Key se purgó a mitad de la "
            "petición — reintenta",
            {"key": key},
        )
    if row.request_hash != request_hash:
        raise ApiError(
            409, "idempotency_conflict",
            "Idempotency-Key reutilizada con un cuerpo distinto",
            {"key": key},
        )
    if row.response is None:
        # Inalcanzable por construcción (no commiteamos reservas desnudas);
        # defensivo: en vuelo ⇒ 409, nunca re-ejecutar.
        raise ApiError(
            409, "idempotency_in_progress",
            "petición con la misma Idempotency-Key aún en curso",
            {"key": key},
        )
    return row.response["status"], row.response["body"]


async def purge_expired(session) -> int:
    """Purga de reservas caducadas (barrido por ix_idem_expires_at). CABLEADA
    en el beat del core-worker vía `jobhunt.idempotency.purge_expired`
    (celery_app.py, cadencia CORE_IDEMPOTENCY_PURGE_EVERY_S) — 2º análisis de
    C-API-W: la deferral a C-2 era huérfana (su DoD son arpones del piloto).
    Además acota la retención del cv_text guardado en `response` al TTL."""
    return (
        await session.execute(
            sa.text(
                "DELETE FROM idempotency_records WHERE expires_at < now()"
            )
        )
    ).rowcount
