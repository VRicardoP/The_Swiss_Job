"""Costura de la capacidad perfiles — A.SEAM (plan §15bis).

Resuelve QUE implementacion sirve cada peticion segun `jobhunt_routing`
(default 'local'). Mapeo modo -> lector, derivado de la matriz de escritor
por estado del plan §15bis (identico al de catalogo/matching):

- local / shadow           -> LocalProfile (el legacy sigue siendo el
                              escritor; en shadow el CDC replica, no cambia
                              lecturas)
- core_read                -> CoreProfile con FALLBACK a local (canary de
                              LECTURAS: el escritor local sigue escribiendo
                              user_profiles => su copia esta completa y el
                              fallback es seguro si el core cae)
- core_primary / rollback_pending -> CoreProfile SIN fallback silencioso
                              (el core es el autoritativo de la lectura;
                              servir el perfil local seria ocultar el fallo —
                              el fallback read-only del cutover es de Fase C)

Como en matching, la resolucion es POR PERFIL: `jobhunt_routing.profile_id`
para SwissJob es `users.id` — la MISMA identidad que la sombra usa como
`external_ref` del perfil core y que `jobhunt_profile_map` usa de clave.

CRITERIO UNIFICADOR (heredado de A.SEAM matching, vigente aqui): mientras el
escritor sea LOCAL (hasta Fase C), nada visible puede ser no-accionable y
ningun estado local puede ser inaccesible. Consecuencias en esta capacidad:
- TODAS las escrituras (PUT profile, CV upload/delete, autofill, weights)
  quedan en local y su respuesta es el recibo del escritor local — nunca
  501/503 por routing (cota Fase C en services/profiles/port.py).
- Los campos del perfil que el core no tiene se sirven del escritor local
  (overlay en CoreProfile) — nunca huecos inventados.
- Sin fila local no se sirve perfil alguno (None -> 404): un perfil visible
  solo-core no admitiria PUT (404 del escritor) — no-accionable.
- GDPR export/delete operan SIEMPRE sobre el almacen local, fuera de la
  costura (exportan/borran lo que este sistema almacena).
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from models.user import User
from services.routing import (
    CAPABILITY_PROFILES,
    MODE_CORE_READ,
    MODE_LOCAL,
    MODE_SHADOW,
    resolve_mode,
)

from .core_client import CoreProfile
from .local import LocalProfile
from .port import CoreUnavailableError, ProfilePort, ProfileUnsupportedError

logger = logging.getLogger(__name__)


class FallbackProfile:
    """Canary de lecturas (core_read): intenta el core y cae al local.

    Tambien cae al local si el core no pudiera servir la operacion con el
    contrato vigente (ProfileUnsupportedError — hoy sin emisor: el overlay
    de CoreProfile ya sirve de local lo que el /v1 no expone)."""

    def __init__(self, primary: ProfilePort, fallback: ProfilePort):
        self._primary = primary
        self._fallback = fallback

    async def get(self, user: User):
        try:
            return await self._primary.get(user)
        except (CoreUnavailableError, ProfileUnsupportedError) as exc:
            self._warn("get", exc)
            return await self._fallback.get(user)

    @staticmethod
    def _warn(op: str, exc: Exception) -> None:
        # Severidades separadas (2ª rev. A.SEAM catalogo, misma regla): el
        # fallback por Unsupported es ESPERADO por contrato y ocurre a ritmo
        # de trafico — a WARNING ahogaria la UNICA senal accionable del
        # canary (CoreUnavailableError = core caido o mal configurado).
        if isinstance(exc, ProfileUnsupportedError):
            logger.debug("perfiles core_read: %s cayo a local (cota /v1: %s)", op, exc)
        else:
            logger.warning("perfiles core_read: %s cayo a local (%s)", op, exc)


async def resolve_profiles(
    db: AsyncSession, user_id: uuid.UUID | None = None
) -> ProfilePort:
    """Puerto de perfiles para esta peticion segun el routing por perfil."""
    mode = await resolve_mode(db, CAPABILITY_PROFILES, user_id)
    if mode in (MODE_LOCAL, MODE_SHADOW):
        return LocalProfile(db)
    if mode == MODE_CORE_READ:
        return FallbackProfile(CoreProfile(db), LocalProfile(db))
    # core_primary / rollback_pending: el core manda (matriz §15bis).
    return CoreProfile(db)
