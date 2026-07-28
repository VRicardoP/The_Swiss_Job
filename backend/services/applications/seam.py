"""Costura de la capacidad candidaturas — A.SEAM (plan §15bis).

Resuelve QUE implementacion sirve cada peticion segun `jobhunt_routing`
(default 'local'). Mapeo modo -> implementacion, derivado de la matriz de
escritor por estado del plan §15bis Y del criterio unificador (heredado de
A.SEAM matching: ningun estado local puede ser inaccesible por el routing):

- local / shadow           -> LocalApplications (el legacy es el motor; en
                              shadow el CDC replica, no cambia lecturas)
- core_read                -> FallbackApplications (canary: intenta el core y
                              cae a local; hoy TODA operacion cae — la cota
                              /v1 es Unsupported total y se registra a DEBUG,
                              severidades del canary heredadas)
- core_primary / rollback_pending -> LocalApplications. CRITERIO UNIFICADOR:
                              el UNICO escritor de TODO el estado de esta
                              capacidad (job_applications + state machine de
                              match_results) es LOCAL hasta Fase C y el /v1
                              no la expone — el estado del escritor local es
                              SIEMPRE accesible; enrutar al core seria 501
                              para estado que solo existe aqui. (A diferencia
                              de catalogo/matching/profiles, donde el core SI
                              sirve la lectura y core_primary va sin fallback
                              silencioso.)

Como en matching/profiles, la resolucion es POR PERFIL:
`jobhunt_routing.profile_id` para SwissJob es `users.id`.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.routing import CAPABILITY_APPLICATIONS, MODE_CORE_READ, resolve_mode

from .core_client import CoreApplications
from .local import LocalApplications
from .port import (
    ApplicationsPort,
    ApplicationsUnsupportedError,
    CoreUnavailableError,
)

logger = logging.getLogger(__name__)


class FallbackApplications:
    """Canary (core_read): intenta el core y cae al local.

    Hoy la cota /v1 es Unsupported TOTAL: toda operacion cae a local a ritmo
    de trafico (DEBUG, esperado por contrato). Se conserva la estructura del
    canary para que, cuando el core publique endpoints de candidatura, la
    unica senal WARNING siga siendo CoreUnavailableError (core caido)."""

    def __init__(self, primary: ApplicationsPort, fallback: ApplicationsPort):
        self._primary = primary
        self._fallback = fallback

    async def list(self, user_id, status=None, limit=50, offset=0):
        try:
            return await self._primary.list(
                user_id, status=status, limit=limit, offset=offset
            )
        except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
            self._warn("list", exc)
            return await self._fallback.list(
                user_id, status=status, limit=limit, offset=offset
            )

    async def create(self, user_id, job_hash, notes=None):
        try:
            return await self._primary.create(user_id, job_hash, notes=notes)
        except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
            self._warn("create", exc)
            return await self._fallback.create(user_id, job_hash, notes=notes)

    async def stats(self, user_id):
        try:
            return await self._primary.stats(user_id)
        except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
            self._warn("stats", exc)
            return await self._fallback.stats(user_id)

    async def update(self, user_id, application_id, changes):
        try:
            return await self._primary.update(user_id, application_id, changes)
        except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
            self._warn("update", exc)
            return await self._fallback.update(user_id, application_id, changes)

    async def delete(self, user_id, application_id):
        try:
            return await self._primary.delete(user_id, application_id)
        except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
            self._warn("delete", exc)
            return await self._fallback.delete(user_id, application_id)

    async def set_match_status(self, user_id, job_hash, application_status):
        try:
            return await self._primary.set_match_status(
                user_id, job_hash, application_status
            )
        except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
            self._warn("set_match_status", exc)
            return await self._fallback.set_match_status(
                user_id, job_hash, application_status
            )

    async def get_match(self, user_id, job_hash):
        try:
            return await self._primary.get_match(user_id, job_hash)
        except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
            self._warn("get_match", exc)
            return await self._fallback.get_match(user_id, job_hash)

    async def save_draft(self, user_id, job_hash, draft):
        try:
            return await self._primary.save_draft(user_id, job_hash, draft)
        except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
            self._warn("save_draft", exc)
            return await self._fallback.save_draft(user_id, job_hash, draft)

    async def get_draft(self, user_id, job_hash):
        try:
            return await self._primary.get_draft(user_id, job_hash)
        except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
            self._warn("get_draft", exc)
            return await self._fallback.get_draft(user_id, job_hash)

    @staticmethod
    def _warn(op: str, exc: Exception) -> None:
        # Severidades separadas (2ª rev. A.SEAM catalogo, misma regla): el
        # fallback por Unsupported es ESPERADO por contrato y ocurre a ritmo
        # de trafico — a WARNING ahogaria la UNICA senal accionable del
        # canary (CoreUnavailableError = core caido o mal configurado).
        if isinstance(exc, ApplicationsUnsupportedError):
            logger.debug(
                "candidaturas core_read: %s cayo a local (cota /v1: %s)", op, exc
            )
        else:
            logger.warning("candidaturas core_read: %s cayo a local (%s)", op, exc)


async def resolve_applications(
    db: AsyncSession, user_id: uuid.UUID | None = None
) -> ApplicationsPort:
    """Puerto de candidaturas para esta peticion segun el routing por perfil."""
    mode = await resolve_mode(db, CAPABILITY_APPLICATIONS, user_id)
    if mode == MODE_CORE_READ:
        return FallbackApplications(CoreApplications(), LocalApplications(db))
    # local / shadow: el legacy es el motor. core_primary / rollback_pending:
    # criterio unificador — todo el estado tiene escritor local UNICO y el
    # /v1 no expone la capacidad => local, nunca 501/503 (docstring modulo).
    return LocalApplications(db)
