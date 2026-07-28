"""Resolucion del routing por perfil+capacidad — A.SEAM (plan §15bis).

Lee `jobhunt_routing` (tabla LOCAL al BFF) y decide que implementacion sirve
cada capacidad para cada perfil. Reglas:

1. Fila exacta (consumer, profile, capability) gana.
2. Si no hay, fila comodin (profile_id = PROFILE_WILDCARD) del consumer.
3. Si no hay ninguna, 'local' (default seguro del plan: todo arranca local y
   sigue sirviendo aunque el core este caido).

Cache corta EN PROCESO con invalidacion TRANSACCIONAL: `set_routing` solo
invalida DESPUES de confirmar el commit (un lector nunca re-cachea el valor
antiguo tras publicarse el nuevo); la TTL acota la staleness entre procesos
o workers distintos.
"""

import logging
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.jobhunt_routing import (
    CONSUMER_SWISSJOB,
    PROFILE_WILDCARD,
    ROUTING_MODES,
    JobhuntRouting,
)

logger = logging.getLogger(__name__)

# Capacidades del plan §15bis (subinterfaces POR CAPACIDAD, no fachada unica).
# Las SEIS tienen costura implementada (services/<capability>). Dos familias:
# - catalog/matching/profiles: el /v1 del core sirve la lectura (canary real).
# - applications/documents/schools: variante LIGERA — el /v1 NO expone la
#   capacidad (cota Unsupported fijada por contract test) y su unico escritor
#   es local => se sirven de local en todos los modos (criterio unificador).
CAPABILITY_CATALOG = "catalog"
CAPABILITY_MATCHING = "matching"
CAPABILITY_PROFILES = "profiles"
CAPABILITY_APPLICATIONS = "applications"
CAPABILITY_DOCUMENTS = "documents"
CAPABILITY_SCHOOLS = "schools"

MODE_LOCAL = "local"
MODE_SHADOW = "shadow"
MODE_CORE_READ = "core_read"
MODE_CORE_PRIMARY = "core_primary"
MODE_ROLLBACK_PENDING = "rollback_pending"

# clave -> (modo, expiracion monotonic)
_cache: dict[tuple[str, uuid.UUID, str], tuple[str, float]] = {}


def invalidate_routing_cache() -> None:
    """Vacia la cache del routing (se llama tras cada commit de cambio)."""
    _cache.clear()


async def resolve_mode(
    db: AsyncSession,
    capability: str,
    profile_id: uuid.UUID | None = None,
    consumer_id: str = CONSUMER_SWISSJOB,
) -> str:
    """Modo de routing para (perfil, capacidad). Sin perfil => comodin."""
    pid = profile_id if profile_id is not None else PROFILE_WILDCARD
    key = (consumer_id, pid, capability)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and hit[1] > now:
        return hit[0]

    rows = (
        await db.execute(
            select(JobhuntRouting.profile_id, JobhuntRouting.mode).where(
                JobhuntRouting.consumer_id == consumer_id,
                JobhuntRouting.capability == capability,
                JobhuntRouting.profile_id.in_([pid, PROFILE_WILDCARD]),
            )
        )
    ).all()
    by_pid = {row.profile_id: row.mode for row in rows}
    mode = by_pid.get(pid) or by_pid.get(PROFILE_WILDCARD) or MODE_LOCAL

    _cache[key] = (mode, now + settings.ROUTING_CACHE_TTL_SECONDS)
    return mode


async def set_routing(
    db: AsyncSession,
    capability: str,
    mode: str,
    profile_id: uuid.UUID | None = None,
    updated_by: str | None = None,
    consumer_id: str = CONSUMER_SWISSJOB,
) -> None:
    """Upsert de una fila de routing + commit + invalidacion de cache.

    La invalidacion es TRANSACCIONAL: solo ocurre tras confirmarse el commit
    (si el commit falla, la cache conserva el estado vigente). `revision` se
    incrementa en cada cambio para auditoria.
    """
    if mode not in ROUTING_MODES:
        raise ValueError(
            f"modo de routing invalido: {mode!r} (validos: {ROUTING_MODES})"
        )
    pid = profile_id if profile_id is not None else PROFILE_WILDCARD
    stmt = (
        pg_insert(JobhuntRouting)
        .values(
            consumer_id=consumer_id,
            profile_id=pid,
            capability=capability,
            mode=mode,
            updated_by=updated_by,
        )
        .on_conflict_do_update(
            index_elements=["consumer_id", "profile_id", "capability"],
            set_={
                "mode": mode,
                "revision": JobhuntRouting.revision + 1,
                "updated_by": updated_by,
                "updated_at": func.now(),
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    invalidate_routing_cache()
    logger.info(
        "jobhunt_routing: %s/%s/%s -> %s (por %s)",
        consumer_id,
        pid,
        capability,
        mode,
        updated_by or "?",
    )
