"""Emisión de eventos de integración al outbox (ADR-05/06, catálogo §3).

Helper ÚNICO para los escritores VIVOS de C-4 (applications/saved_searches):
`event_id` determinista (uuid5 sobre type + clave natural — el MISMO namespace
que matching.py), insert en la MISMA transacción de la escritura con
ON CONFLICT DO NOTHING, y fila de entrega por destino (el BFF del consumidor
del perfil). Reintentar la misma mutación no re-emite.
"""

import json
import uuid

import sqlalchemy as sa

from jobhunt_core.matching import event_id_for


async def emit(
    session,
    *,
    event_type: str,
    natural_key: str,
    aggregate: str,
    aggregate_id: uuid.UUID,
    subject_profile_id: uuid.UUID,
    version: int,
    payload: dict,
    destination: str,
) -> uuid.UUID:
    """Inserta el evento + su entrega (misma tx del llamador; sin commit)."""
    eid = event_id_for(event_type, natural_key)
    await session.execute(
        sa.text(
            "INSERT INTO integration_outbox "
            "(event_id, aggregate, aggregate_id, subject_profile_id, "
            " version, type, payload) "
            "VALUES (:eid, :agg, :aggid, :pid, :ver, :type, "
            "CAST(:payload AS jsonb)) "
            "ON CONFLICT (event_id) DO NOTHING"
        ),
        {
            "eid": eid, "agg": aggregate, "aggid": aggregate_id,
            "pid": subject_profile_id, "ver": version, "type": event_type,
            "payload": json.dumps(payload, ensure_ascii=False, default=str),
        },
    )
    await session.execute(
        sa.text(
            "INSERT INTO integration_outbox_deliveries "
            "(event_id, destination, next_attempt_at) "
            "VALUES (:eid, :dest, clock_timestamp()) "
            "ON CONFLICT (event_id, destination) DO NOTHING"
        ),
        {"eid": eid, "dest": destination},
    )
    return eid
