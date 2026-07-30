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
3. Si hay CONFLICTO: Postgres hizo BLOQUEAR nuestro INSERT hasta que la
   transacción dueña terminó — ESPERA ACOTADA por la duración del handler
   (decisión documentada frente al 409-en-vuelo: un reintento legítimo espera
   y recibe la MISMA respuesta, en vez de un 409 que el cliente tendría que
   reintentar). Al resolverse la fila dueña ya está COMMITEADA:
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

from jobhunt_core.api.deps import ApiError

logger = logging.getLogger(__name__)

# TTL de la reserva: cota para la purga (no para la validez de la respuesta —
# mientras la fila viva, un reintento la replica). 24h cubre de sobra un
# reintento de red del BFF.
IDEM_TTL = timedelta(hours=24)
KEY_MAX_LEN = 200  # = longitud de idempotency_records.key


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
    expires = datetime.now(timezone.utc) + IDEM_TTL
    keys = {"cid": principal.consumer_id, "k": key, "r": route}

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
    row = (
        await session.execute(
            sa.text(
                "SELECT request_hash, response FROM idempotency_records "
                "WHERE consumer_id = :cid AND key = :k AND route = :r"
            ),
            keys,
        )
    ).one()
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
