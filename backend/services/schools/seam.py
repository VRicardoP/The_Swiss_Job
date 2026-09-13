"""School routing, E.15: fresh per-profile override > wildcard > local.

local/shadow read static BFF configuration; core_read is a read canary
with observable fallback. core_primary/rollback_pending read only core
monitors. School state/preferences use the same authority in state.py.
Swiss routing profile_id is users.id, not the core profile UUID.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.routing import MODE_CORE_READ
from .state import read_school_mode

from .core_client import CoreSchools
from .local import LocalSchools
from .port import CoreUnavailableError, SchoolsPort, SchoolsUnsupportedError

logger = logging.getLogger(__name__)


class FallbackSchools:
    """Read canary: actual core HTTP, observable fallback on unavailability."""

    def __init__(self, primary: SchoolsPort, fallback: SchoolsPort):
        self._primary = primary
        self._fallback = fallback

    async def list(self):
        try:
            return await self._primary.list()
        except (CoreUnavailableError, SchoolsUnsupportedError) as exc:
            self._warn("list", exc)
            return await self._fallback.list()

    @staticmethod
    def _warn(op: str, exc: Exception) -> None:
        # Severidades separadas (2ª rev. A.SEAM catalogo, misma regla): el
        # fallback por Unsupported es ESPERADO por contrato y ocurre a ritmo
        # de trafico — a WARNING ahogaria la UNICA senal accionable del
        # canary (CoreUnavailableError = core caido o mal configurado).
        if isinstance(exc, SchoolsUnsupportedError):
            logger.debug("colegios core_read: %s cayo a local (cota /v1: %s)", op, exc)
        else:
            logger.warning("colegios core_read: %s cayo a local (%s)", op, exc)


async def resolve_schools(
    db: AsyncSession, user_id: uuid.UUID | None = None
) -> SchoolsPort:
    """Puerto de colegios para esta peticion segun el routing por perfil."""
    mode = await read_school_mode(db, user_id)
    if mode == MODE_CORE_READ:
        return FallbackSchools(CoreSchools(enabled=True), LocalSchools())
    # No local fallback once the migrated school authority is core.
    if mode in ("core_primary", "rollback_pending"):
        return CoreSchools(enabled=True)
    return LocalSchools()
