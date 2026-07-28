"""Costura de la capacidad catalogo — A.SEAM (plan §15bis).

Resuelve QUE implementacion sirve cada peticion segun `jobhunt_routing`
(default 'local'). Mapeo modo -> lector, derivado de la matriz de escritor
por estado del plan §15bis:

- local / shadow           -> LocalCatalog (el legacy sigue siendo el motor;
                              en shadow el outbox replica, no cambia lecturas)
- core_read                -> CoreCatalog con FALLBACK a local (canary de
                              LECTURAS: el legacy aun escribe, su copia esta
                              completa => fallback seguro si el core cae)
- core_primary / rollback_pending -> CoreCatalog SIN fallback silencioso (el
                              core es el autoritativo; servir datos locales
                              desactualizados seria mentir — fallback
                              read-only del cutover queda para Fase C)
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from services.routing import (
    CAPABILITY_CATALOG,
    MODE_CORE_READ,
    MODE_LOCAL,
    MODE_SHADOW,
    resolve_mode,
)

from .core_client import CoreCatalog
from .local import LocalCatalog
from .port import CatalogPort, CatalogUnsupportedError, CoreUnavailableError

logger = logging.getLogger(__name__)


class FallbackCatalog:
    """Canary de lecturas (core_read): intenta el core y cae al local.

    Tambien cae al local cuando el core no soporta la operacion (busqueda,
    stats...) o no conoce la referencia (identidades MD5 legacy conviven con
    UUIDs del core durante el canary).
    """

    def __init__(self, primary: CatalogPort, fallback: CatalogPort):
        self._primary = primary
        self._fallback = fallback

    async def search(self, params):
        try:
            return await self._primary.search(params)
        except (CoreUnavailableError, CatalogUnsupportedError) as exc:
            self._warn("search", exc)
            return await self._fallback.search(params)

    async def stats(self):
        try:
            return await self._primary.stats()
        except (CoreUnavailableError, CatalogUnsupportedError) as exc:
            self._warn("stats", exc)
            return await self._fallback.stats()

    async def sources(self):
        try:
            return await self._primary.sources()
        except (CoreUnavailableError, CatalogUnsupportedError) as exc:
            self._warn("sources", exc)
            return await self._fallback.sources()

    async def get(self, job_ref: str):
        try:
            result = await self._primary.get(job_ref)
        except (CoreUnavailableError, CatalogUnsupportedError) as exc:
            self._warn("get", exc)
            return await self._fallback.get(job_ref)
        if result is None:
            # Referencia desconocida para el core (p.ej. hash MD5 legacy):
            # durante el canary la sirve el motor local si la tiene.
            return await self._fallback.get(job_ref)
        return result

    @staticmethod
    def _warn(op: str, exc: Exception) -> None:
        # 2ª rev. A.SEAM: el fallback por Unsupported es ESPERADO por contrato
        # (el /v1 aún no expone search/stats/sources) y ocurre a ritmo de
        # tráfico — a WARNING ahogaba la ÚNICA señal accionable del canary
        # (CoreUnavailableError = core caído). Severidades separadas.
        if isinstance(exc, CatalogUnsupportedError):
            logger.debug("catalogo core_read: %s cayo a local (cota /v1: %s)", op, exc)
        else:
            logger.warning("catalogo core_read: %s cayo a local (%s)", op, exc)


async def resolve_catalog(db: AsyncSession, profile_id=None) -> CatalogPort:
    """Puerto de catalogo para esta peticion segun el routing por perfil."""
    mode = await resolve_mode(db, CAPABILITY_CATALOG, profile_id)
    if mode in (MODE_LOCAL, MODE_SHADOW):
        return LocalCatalog(db)
    if mode == MODE_CORE_READ:
        return FallbackCatalog(CoreCatalog(), LocalCatalog(db))
    # core_primary / rollback_pending: el core manda (matriz §15bis).
    return CoreCatalog()
