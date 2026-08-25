"""Resolucion de identidad usuario legacy -> perfil core — A.SEAM.

Lee/escribe `jobhunt_profile_map` (tabla LOCAL al BFF; racional en
models/jobhunt_profile_map.py). El vinculo es POR USUARIO, no por capacidad:
la misma fila la consumen matching (services/matching), perfiles
(services/profiles) y candidaturas (services/applications, escrituras C-4).
Sin cache: se consulta una vez por peticion enrutada al
core, y el enrolamiento es una operacion de operador.
"""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.jobhunt_profile_map import JobhuntProfileMap

logger = logging.getLogger(__name__)


async def resolve_core_profile_id(
    db: AsyncSession, user_id: uuid.UUID
) -> uuid.UUID | None:
    """UUID del perfil en el core para este usuario, o None si no esta
    enrolado (el cliente core lo trata como indisponibilidad SIN red)."""
    return (
        await db.execute(
            select(JobhuntProfileMap.core_profile_id).where(
                JobhuntProfileMap.user_id == user_id
            )
        )
    ).scalar_one_or_none()


async def set_profile_link(
    db: AsyncSession,
    user_id: uuid.UUID,
    core_profile_id: uuid.UUID,
    updated_by: str | None = None,
) -> None:
    """Upsert del vinculo + commit (operacion de enrolamiento del canary,
    simetrica a services.routing.set_routing)."""
    stmt = (
        pg_insert(JobhuntProfileMap)
        .values(
            user_id=user_id,
            core_profile_id=core_profile_id,
            updated_by=updated_by,
        )
        .on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "core_profile_id": core_profile_id,
                "updated_by": updated_by,
                "updated_at": func.now(),
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    logger.info(
        "jobhunt_profile_map: %s -> %s (por %s)",
        user_id,
        core_profile_id,
        updated_by or "?",
    )
