"""Costura de la capacidad candidaturas — C-4 (escrituras /v1), plan §15bis.

Resuelve QUE implementacion sirve cada peticion segun `jobhunt_routing`
(default 'local'). Las candidaturas son ESTADO DURABLE del que esta capacidad
es DUEÑA: el modo decide QUIEN es el ESCRITOR autoritativo del CRUD de
`job_applications` (matriz de escritor por estado del plan §15bis). Mapeo:

- local / shadow  -> LocalApplications (el legacy es el escritor; en shadow el
                     CDC replica legacy→core — la replicacion no es asunto de
                     la costura: lecturas y escrituras no cambian).
- core_read       -> LocalApplications: DECLARADO EQUIVALENTE a local. Para un
                     durable de escritor local NO existe un canary sensato:
                     leer del core (replica que aun no recibe las escrituras
                     vivas) mientras se escribe en local romperia
                     read-your-writes en un CRUD interactivo, y un canary de
                     ESCRITURAS crearia un segundo escritor (split-brain). El
                     diseño C-4 lo fija (DISENO_C4_ESCRITURAS_V2_1, Alcance):
                     el core es sistema de registro solo en core_primary.
- core_primary / rollback_pending -> CoreWriterApplications: el core es el
                     escritor autoritativo del CRUD (en rollback_pending lo
                     SIGUE siendo hasta el replay final — matriz §15bis) SIN
                     fallback silencioso — escribir en local "porque el core
                     no responde" bifurcaria el estado; el 503/501 honesto del
                     router es preferible. EXCEPCION por el criterio
                     unificador (ningun estado local puede ser inaccesible por
                     el routing): el state machine sobre `match_results`
                     (status/draft de watchlist) es CO-PROPIEDAD de la
                     capacidad MATCHING con escritor LOCAL y SIN superficie
                     C-4 (inventario de escritores en port.py) — esas 4
                     operaciones se sirven SIEMPRE de local, tambien en
                     core_primary. Su flip es cota de Fase C (exige que
                     _save_results y el prune de maintenance lean el estado
                     del escritor activo).

Como en matching/profiles, la resolucion es POR PERFIL:
`jobhunt_routing.profile_id` para SwissJob es `users.id`; la identidad
usuario→perfil core la resuelve el cliente (jobhunt_profile_map, NO comodin).
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.routing import (
    CAPABILITY_APPLICATIONS,
    MODE_CORE_PRIMARY,
    MODE_ROLLBACK_PENDING,
    resolve_mode,
)

from .core_client import CoreApplications
from .local import LocalApplications
from .port import ApplicationsPort


class CoreWriterApplications:
    """Escritor core (core_primary/rollback_pending): CRUD+stats de
    `job_applications` contra el cliente C-4 SIN fallback silencioso; el
    state machine sobre `match_results` SIEMPRE del escritor local (criterio
    unificador — docstring del modulo)."""

    def __init__(self, core: CoreApplications, local: LocalApplications):
        self._core = core
        self._local = local

    # ── CRUD + stats: escritor CORE (sin fallback silencioso) ───────────

    async def list(self, user_id, status=None, limit=50, offset=0):
        return await self._core.list(user_id, status=status, limit=limit, offset=offset)

    async def create(self, user_id, job_hash, notes=None):
        return await self._core.create(user_id, job_hash, notes=notes)

    async def stats(self, user_id):
        return await self._core.stats(user_id)

    async def update(self, user_id, application_id, changes):
        return await self._core.update(user_id, application_id, changes)

    async def delete(self, user_id, application_id):
        return await self._core.delete(user_id, application_id)

    # ── State machine match_results: escritor LOCAL (criterio unificador) ──

    async def set_match_status(self, user_id, job_hash, application_status):
        return await self._local.set_match_status(user_id, job_hash, application_status)

    async def get_match(self, user_id, job_hash):
        return await self._local.get_match(user_id, job_hash)

    async def save_draft(self, user_id, job_hash, draft):
        return await self._local.save_draft(user_id, job_hash, draft)

    async def get_draft(self, user_id, job_hash):
        return await self._local.get_draft(user_id, job_hash)


async def resolve_applications(
    db: AsyncSession, user_id: uuid.UUID | None = None
) -> ApplicationsPort:
    """Puerto de candidaturas para esta peticion segun el routing por perfil."""
    mode = await resolve_mode(db, CAPABILITY_APPLICATIONS, user_id)
    if mode in (MODE_CORE_PRIMARY, MODE_ROLLBACK_PENDING):
        return CoreWriterApplications(CoreApplications(db), LocalApplications(db))
    # local / shadow / core_read: el legacy es el escritor (core_read
    # declarado equivalente — docstring del modulo).
    return LocalApplications(db)
