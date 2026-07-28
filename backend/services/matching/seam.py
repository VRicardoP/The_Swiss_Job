"""Costura de la capacidad matching — A.SEAM (plan §15bis).

Resuelve QUE implementacion sirve cada peticion segun `jobhunt_routing`
(default 'local'). Mapeo modo -> lector, derivado de la matriz de escritor
por estado del plan §15bis (identico al de catalogo):

- local / shadow           -> LocalMatching (el legacy sigue siendo el motor;
                              en shadow el CDC replica, no cambia lecturas)
- core_read                -> CoreMatching con FALLBACK a local (canary de
                              LECTURAS: el motor legacy sigue escribiendo
                              match_results => su copia esta completa y el
                              fallback es seguro si el core cae)
- core_primary / rollback_pending -> CoreMatching SIN fallback silencioso
                              (el core es el autoritativo; servir el feed
                              local desactualizado seria mentir — el
                              fallback read-only del cutover es de Fase C)

A diferencia de catalogo, aqui la resolucion es POR PERFIL: el canary de
matching es multiusuario (plan §15bis) y `jobhunt_routing.profile_id` para
SwissJob es `users.id` — la MISMA identidad que la sombra usa como
`external_ref` del perfil core y que `jobhunt_profile_map` usa de clave.

CRITERIO UNIFICADOR (1ª rev. A.SEAM matching): mientras el escritor sea
LOCAL (hasta Fase C), nada visible puede ser no-accionable y ningun estado
local puede ser inaccesible. Consecuencias en esta etapa:
- 'saved' es proyeccion PURA del estado del escritor local (feedback
  positivo): se sirve de LOCAL en TODOS los modos (CoreMatching.saved
  delega en el escritor) — nunca 501 por routing.
- El feed servido por el core EXCLUYE los items sin respaldo local
  accionable (core-nativos o sin fila en `jobs`): mostrarlos permitiria un
  "not for me" que devuelve 404. Cota registrada: reapareceran en Fase C,
  cuando el flip de escritor (escritura sincrona + idempotency key) los
  haga accionables.
- El feedback sobre un huerfano legacy (Job local SIN fila MatchResult,
  visible via feed del core) upserta una fila minima en el escritor local
  (MatchResultService.submit_feedback): "not for me" desaparece tambien ahi.

Cota registrada (NO implementada): los schedulers legacy (matching_tasks,
digest) siguen ejecutando para TODOS los perfiles en esta etapa — correcto
mientras el legacy sea el escritor (local/shadow/core_read: su copia debe
seguir completa para el fallback). La consciencia de routing de los
schedulers (omitir perfiles en core_primary) llega con el flip de escritor
en Fase C, junto a la idempotency key de las escrituras interactivas.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.routing import (
    CAPABILITY_MATCHING,
    MODE_CORE_READ,
    MODE_LOCAL,
    MODE_SHADOW,
    resolve_mode,
)

from .core_client import CoreMatching
from .local import LocalMatching
from .port import CoreUnavailableError, MatchingPort, MatchingUnsupportedError

logger = logging.getLogger(__name__)


class FallbackMatching:
    """Canary de lecturas (core_read): intenta el core y cae al local.

    Tambien cae al local si el core no pudiera servir una operacion con el
    contrato vigente (MatchingUnsupportedError — hoy sin emisor: `saved` ya
    se sirve del escritor local en todos los modos, criterio unificador).
    """

    def __init__(self, primary: MatchingPort, fallback: MatchingPort):
        self._primary = primary
        self._fallback = fallback

    async def results(self, user_id, limit: int = 20, offset: int = 0):
        try:
            return await self._primary.results(user_id, limit=limit, offset=offset)
        except (CoreUnavailableError, MatchingUnsupportedError) as exc:
            self._warn("results", exc)
            return await self._fallback.results(user_id, limit=limit, offset=offset)

    async def saved(self, user_id, limit: int = 100, offset: int = 0):
        try:
            return await self._primary.saved(user_id, limit=limit, offset=offset)
        except (CoreUnavailableError, MatchingUnsupportedError) as exc:
            self._warn("saved", exc)
            return await self._fallback.saved(user_id, limit=limit, offset=offset)

    @staticmethod
    def _warn(op: str, exc: Exception) -> None:
        # Severidades separadas (2ª rev. A.SEAM catalogo, misma regla): el
        # fallback por Unsupported es ESPERADO por contrato y ocurre a ritmo
        # de trafico — a WARNING ahogaria la UNICA senal accionable del
        # canary (CoreUnavailableError = core caido o mal configurado).
        if isinstance(exc, MatchingUnsupportedError):
            logger.debug("matching core_read: %s cayo a local (cota /v1: %s)", op, exc)
        else:
            logger.warning("matching core_read: %s cayo a local (%s)", op, exc)


async def resolve_matching(
    db: AsyncSession, user_id: uuid.UUID | None = None
) -> MatchingPort:
    """Puerto de matching para esta peticion segun el routing por perfil."""
    mode = await resolve_mode(db, CAPABILITY_MATCHING, user_id)
    if mode in (MODE_LOCAL, MODE_SHADOW):
        return LocalMatching(db)
    if mode == MODE_CORE_READ:
        return FallbackMatching(CoreMatching(db), LocalMatching(db))
    # core_primary / rollback_pending: el core manda (matriz §15bis).
    return CoreMatching(db)
