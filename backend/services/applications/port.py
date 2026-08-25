"""Puerto de la capacidad CANDIDATURAS — A.SEAM (plan §15bis).

Subinterfaz POR CAPACIDAD (no fachada unica `JobHunting`). Las operaciones
son exactamente las que hoy consumen los routers de candidatura:
`routers/applications.py` (CRUD + stats de `job_applications`) y
`routers/watchlist.py` (state machine de candidatura sobre `match_results`:
status, borrador de carta y su lectura para calendario).

CONTRATO /v1 (C-4, DISENO_C4_ESCRITURAS_V2_1): el core SI expone el CRUD de
candidaturas — GET/POST /v1/applications, PATCH/DELETE /v1/applications/{id}
(jobhunt_core/api/v1_applications.py) — y `CoreApplications` (core_client.py)
es su cliente HTTP real (identidad de perfil POR USUARIO via
jobhunt_profile_map). El state machine sobre match_results (4 ultimas
operaciones del puerto) NO tiene superficie C-4: en el cliente core es
ApplicationsUnsupportedError y la costura lo sirve SIEMPRE de local.

CRITERIO UNIFICADOR (heredado de A.SEAM matching): ningun estado local puede
ser inaccesible por el routing. Con C-4, el modo decide el ESCRITOR del CRUD
de `job_applications` (core_primary/rollback_pending => core sin fallback
silencioso; resto => local, con core_read declarado equivalente a local —
seam.py); el estado de `match_results` conserva escritor LOCAL unico y se
sirve de local en TODOS los modos.

INVENTARIO DE ESCRITORES (1ª rev. A.SEAM final — exacto, para el flip de
Fase C): (a) los routers via esta costura (status/draft/CRUD); (b) la fila
match_results que SOSTIENE application_status/draft_letter es CO-PROPIEDAD
de la capacidad MATCHING: match_service._save_results crea/actualiza/poda
su ciclo de vida (preservando estos campos), y maintenance_tasks decide
archivar-vs-borrar la oferta GATEANDO sobre application_status<>'detected'
OR draft_letter IS NOT NULL. COTA DE FLIP Fase C: mover el escritor de
estos campos EXIGE que _save_results y el prune de maintenance lean el
estado del escritor ACTIVO (no la copia local congelada) — si no, una fila
con engagement real en el core pareceria 'limpia' en local y se BORRARIA
(split-brain con perdida de candidatura).

Dos implementaciones detras del mismo puerto:
- `LocalApplications` (services/applications/local.py): logica actual de los
  routers, movida verbatim.
- `CoreApplications` (services/applications/core_client.py): cliente HTTP
  real de los endpoints C-4 (con las cotas documentadas en su docstring).
La eleccion la decide `jobhunt_routing` (services/applications/seam.py), que
en modo escritor-core compone ambas (CoreWriterApplications).
"""

import uuid
from typing import Protocol

from models.enums import ApplicationStatus
from schemas.applications import (
    ApplicationResponse,
    ApplicationsListResponse,
    ApplicationStatsResponse,
    ApplicationUpdate,
)


class ApplicationsError(Exception):
    """Base de errores de la capacidad candidaturas."""


class CoreUnavailableError(ApplicationsError):
    """El core no responde, fallo, devolvio un payload fuera de contrato o
    falta la credencial/vinculo de identidad (CORE_CONSUMER_KEY /
    jobhunt_profile_map). El router lo traduce a 503 — solo alcanzable con
    routing core_primary/rollback_pending (sin fallback silencioso)."""


class ApplicationsUnsupportedError(ApplicationsError):
    """La operacion (o alguno de sus campos) no existe en el contrato /v1 del
    core: el state machine de match_results y `applied_url` (el snapshot C-4
    es inmutable; el PATCH solo muta status/notes/follow_up_date). El router
    lo traduce a 501 (cota honesta)."""


class ApplicationJobNotFoundError(ApplicationsError):
    """La oferta referenciada al crear la candidatura no existe (=> 404)."""


class DuplicateApplicationError(ApplicationsError):
    """Ya existe candidatura del usuario para esa oferta (=> 409)."""


class ApplicationsPort(Protocol):
    """Operaciones de lectura Y escritura del estado de candidatura."""

    # ── CRUD + stats de job_applications (routers/applications.py) ──────

    async def list(
        self,
        user_id: uuid.UUID,
        status: ApplicationStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ApplicationsListResponse:
        """Candidaturas del usuario, orden updated_at DESC, con resumen
        by_status (la validacion del filtro sigue en el router)."""
        ...

    async def create(
        self, user_id: uuid.UUID, job_hash: str, notes: str | None = None
    ) -> ApplicationResponse:
        """Alta con status=saved. Levanta ApplicationJobNotFoundError si la
        oferta no existe y DuplicateApplicationError si ya hay candidatura."""
        ...

    async def stats(self, user_id: uuid.UUID) -> ApplicationStatsResponse:
        """Pipeline por estado/fuente + tasas de conversion."""
        ...

    async def update(
        self,
        user_id: uuid.UUID,
        application_id: uuid.UUID,
        changes: ApplicationUpdate,
    ) -> ApplicationResponse | None:
        """Actualizacion parcial (auto-transicion applied_at incluida).
        None si no existe para ese usuario (el router lo hace 404)."""
        ...

    async def delete(self, user_id: uuid.UUID, application_id: uuid.UUID) -> bool:
        """Borrado; False si no existe para ese usuario (=> 404)."""
        ...

    # ── State machine sobre match_results (routers/watchlist.py) ────────

    async def set_match_status(
        self, user_id: uuid.UUID, job_hash: str, application_status: str
    ) -> bool:
        """Transicion del state machine; False si no hay match (=> 404)."""
        ...

    async def get_match(self, user_id: uuid.UUID, job_hash: str):
        """(MatchResult, Job) de la candidatura, o None (=> 404). Lo consumen
        la generacion de borrador y el export de calendario."""
        ...

    async def save_draft(self, user_id: uuid.UUID, job_hash: str, draft: str) -> bool:
        """Persiste el borrador y avanza detected/reviewed -> drafted.
        False si no hay match (=> 404)."""
        ...

    async def get_draft(self, user_id: uuid.UUID, job_hash: str) -> str | None:
        """Borrador guardado, o None/vacio si no lo hay (=> 404)."""
        ...
