"""Proyección de los filtros de exclusión del BFF al core (hallazgo B de la
revisión externa 2026-09-07).

Por qué existe: el core SIRVE el feed y aplica las exclusiones en su
recuperación de candidatos, pero el BFF es donde el usuario las crea, aprueba
y BORRA. Mientras el BFF solo escribía en su tabla legacy y el core recibía
únicamente altas por un importador, una baja no llegaba nunca: la regla
seguía excluyendo ofertas para siempre.

Contrato: se empuja el conjunto COMPLETO y activo (`PUT
/v1/profiles/{pid}/exclusions`, declarativo). Altas y bajas viajan por el
mismo camino, así que no hay operación que pueda perderse. Solo se proyecta
si el matching de ese perfil lo sirve el core (`legacy_owns` falso); si el
perfil sigue en local, el motor legacy ya aplica sus filtros y no hay nada
que sincronizar.

Fallar aquí NO puede tumbar la operación del usuario sobre su filtro: se
registra y se deja la señal, porque el estado autoritativo local ya está
escrito y el siguiente empujón reconcilia (es declarativo).
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.job_filter import JobFilter
from services.matching.identity import resolve_core_profile_id
from services.profiles.core_client import default_client_factory
from services.routing import CAPABILITY_MATCHING, legacy_owns, resolve_mode

logger = logging.getLogger(__name__)


async def sync_exclusions_to_core(
    db: AsyncSession, user_id: uuid.UUID, client_factory=None
) -> dict:
    """Empuja al core el conjunto activo de filtros del usuario."""
    mode = await resolve_mode(db, CAPABILITY_MATCHING, user_id)
    if legacy_owns(mode):
        return {"status": "local", "mode": mode}
    core_profile_id = await resolve_core_profile_id(db, user_id)
    if core_profile_id is None:
        logger.warning(
            "exclusions_sync: usuario %s sin vinculo en jobhunt_profile_map "
            "— sus filtros NO llegan al core", user_id)
        return {"status": "sin_vinculo"}
    filas = (
        await db.execute(
            select(JobFilter).where(
                JobFilter.user_id == user_id,
                JobFilter.is_active.is_(True),
            )
        )
    ).scalars().all()
    payload = {
        "exclusions": [
            {"kind": f.filter_type, "pattern": f.pattern}
            for f in filas
            if f.filter_type in ("title_contains", "tag_contains")
            and (f.pattern or "").strip()
        ]
    }
    factory = client_factory or default_client_factory
    try:
        async with factory() as client:
            resp = await client.put(
                f"/profiles/{core_profile_id}/exclusions", json=payload)
        if resp.status_code >= 400:
            logger.error(
                "exclusions_sync: el core rechazo la proyeccion (%s): %s",
                resp.status_code, resp.text[:200])
            return {"status": "rechazado", "http": resp.status_code}
    except Exception as exc:  # red/core caido: se reintenta al siguiente cambio
        logger.error("exclusions_sync: core inaccesible: %s", exc)
        return {"status": "core_inaccesible"}
    return {"status": "ok", "declaradas": len(payload["exclusions"])}
