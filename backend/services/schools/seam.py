"""Costura de la capacidad colegios — A.SEAM (plan §15bis).

Resuelve QUE implementacion sirve cada peticion segun `jobhunt_routing`
(default 'local'). Mapeo modo -> implementacion, derivado de la matriz de
escritor por estado del plan §15bis Y del criterio unificador (heredado de
A.SEAM matching: ningun estado local puede ser inaccesible por el routing):

- local / shadow           -> LocalSchools (config estatica del BFF)
- core_read                -> FallbackSchools (canary: intenta el core y cae
                              a local; hoy TODA operacion cae — la cota /v1
                              es Unsupported total y se registra a DEBUG,
                              severidades del canary heredadas)
- core_primary / rollback_pending -> LocalSchools. CRITERIO UNIFICADOR: el
                              unico escritor del estado (la config de
                              colegios del propio BFF) es LOCAL y el /v1 no
                              expone la capacidad — el estado del escritor
                              local es SIEMPRE accesible; enrutar al core
                              seria 501 para estado que solo existe aqui.

La resolucion es POR PERFIL (el listado exige usuario autenticado):
`jobhunt_routing.profile_id` para SwissJob es `users.id`.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.routing import CAPABILITY_SCHOOLS, MODE_CORE_READ, resolve_mode

from .core_client import CoreSchools
from .local import LocalSchools
from .port import CoreUnavailableError, SchoolsPort, SchoolsUnsupportedError

logger = logging.getLogger(__name__)


class FallbackSchools:
    """Canary (core_read): intenta el core y cae al local.

    Hoy la cota /v1 es Unsupported TOTAL: toda operacion cae a local a ritmo
    de trafico (DEBUG, esperado por contrato). Se conserva la estructura del
    canary para que, cuando el core publique su endpoint de colegios, la
    unica senal WARNING siga siendo CoreUnavailableError (core caido)."""

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
    mode = await resolve_mode(db, CAPABILITY_SCHOOLS, user_id)
    if mode == MODE_CORE_READ:
        return FallbackSchools(CoreSchools(), LocalSchools())
    # local / shadow: config del BFF. core_primary / rollback_pending:
    # criterio unificador — el escritor del estado es local UNICO y el /v1
    # no expone la capacidad => local, nunca 501/503 (docstring modulo).
    return LocalSchools()
