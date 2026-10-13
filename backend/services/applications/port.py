"""Port for applications and retained MatchResult application state.

SwissJob is the fixed authority for application CRUD, status, drafts and
calendar data. The interface keeps router validation separate from storage.
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
