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
from collections.abc import Iterable

from sqlalchemy import ColumnElement, func, select
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

# Modos en los que el motor LEGACY es autoritativo y sus schedulers ACTUAN
# (matriz de escritor del §15bis): en 'local' es el unico motor; en 'shadow'
# el core solo observa (proyeccion CDC), asi que legacy sigue emitiendo. En
# 'core_read'/'core_primary' el escritor de matching/notificaciones es el
# core, y en 'rollback_pending' LO SIGUE SIENDO hasta el replay final —
# actuar ahi duplicaria matching y correo al usuario.
LEGACY_OWNED_MODES = (MODE_LOCAL, MODE_SHADOW)


def legacy_owns(mode: str) -> bool:
    """True si el motor legacy es autoritativo en `mode` (schedulers actuan)."""
    return mode in LEGACY_OWNED_MODES


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


async def resolve_modes(
    db: AsyncSession,
    capability: str,
    profile_ids: Iterable[uuid.UUID],
    consumer_id: str = CONSUMER_SWISSJOB,
) -> dict[uuid.UUID, str]:
    """Resolucion MASIVA: modo por perfil en UNA sola consulta.

    Para los schedulers (gate anti-doble-motor D.1): resolver N perfiles con
    `resolve_mode` en un bucle serian N consultas. Misma precedencia que
    `resolve_mode` (fila exacta > comodin > 'local') y la MISMA cache por
    clave (consumer, perfil, capacidad): los hits vigentes no tocan la BD.

    La consulta trae TODAS las filas de la capacidad, no las de los perfiles
    pedidos: durante un canary son un punado (una por perfil migrado + la
    comodin) y, en el peor caso, una por perfil — lo mismo que devolveria un
    IN, pero sin enviar N parametros ni chocar con el limite de 65535 de
    Postgres cuando la instalacion crece. Lista vacia => {} sin tocar la BD.
    """
    ids = list(dict.fromkeys(profile_ids))  # dedupe preservando el orden
    if not ids:
        return {}

    now = time.monotonic()
    resolved: dict[uuid.UUID, str] = {}
    missing: list[uuid.UUID] = []
    for pid in ids:
        hit = _cache.get((consumer_id, pid, capability))
        if hit is not None and hit[1] > now:
            resolved[pid] = hit[0]
        else:
            missing.append(pid)
    if not missing:
        return resolved

    rows = (
        await db.execute(
            select(JobhuntRouting.profile_id, JobhuntRouting.mode).where(
                JobhuntRouting.consumer_id == consumer_id,
                JobhuntRouting.capability == capability,
            )
        )
    ).all()
    by_pid = {row.profile_id: row.mode for row in rows}
    wildcard_mode = by_pid.get(PROFILE_WILDCARD)
    expiry = now + settings.ROUTING_CACHE_TTL_SECONDS
    for pid in missing:
        mode = by_pid.get(pid) or wildcard_mode or MODE_LOCAL
        resolved[pid] = mode
        _cache[(consumer_id, pid, capability)] = (mode, expiry)
    return resolved


def legacy_owned_sql(
    profile_id_col: ColumnElement[uuid.UUID],
    capability: str,
    consumer_id: str = CONSUMER_SWISSJOB,
) -> ColumnElement[bool]:
    """Clausula SQL «el perfil de esta columna esta en modo legacy-owned».

    Para filtrar EN la consulta (el descarte ocurre en la BD, sin materializar
    filas de perfiles migrados — p. ej. el digest diario). Reproduce la
    precedencia exacta > comodin > 'local'.

    La fila EXACTA se busca con una subconsulta correlacionada (una busqueda
    por la PK (consumer, perfil, capacidad) por fila), pero la del COMODIN es
    la MISMA para todas: va como subconsulta NO correlacionada, que Postgres
    evalua una sola vez por sentencia en lugar de una vez por fila.
    No pasa por la cache en proceso: lee el estado comprometido en BD, que
    nunca es mas viejo que lo cacheado.
    """
    exacta = (
        select(JobhuntRouting.mode)
        .where(
            JobhuntRouting.consumer_id == consumer_id,
            JobhuntRouting.capability == capability,
            JobhuntRouting.profile_id == profile_id_col,
        )
        .scalar_subquery()
    )
    comodin = (
        select(JobhuntRouting.mode)
        .where(
            JobhuntRouting.consumer_id == consumer_id,
            JobhuntRouting.capability == capability,
            JobhuntRouting.profile_id == PROFILE_WILDCARD,
        )
        .scalar_subquery()
    )
    return func.coalesce(exacta, comodin, MODE_LOCAL).in_(LEGACY_OWNED_MODES)


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
