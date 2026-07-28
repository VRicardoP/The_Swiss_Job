"""Puerto de la capacidad CANDIDATURAS — A.SEAM (plan §15bis).

Subinterfaz POR CAPACIDAD (no fachada unica `JobHunting`). Las operaciones
son exactamente las que hoy consumen los routers de candidatura:
`routers/applications.py` (CRUD + stats de `job_applications`) y
`routers/watchlist.py` (state machine de candidatura sobre `match_results`:
status, borrador de carta y su lectura para calendario).

VARIANTE LIGERA de la costura: el /v1 del core NO expone candidaturas
(jobhunt_core/api/v1.py solo sirve vacancies/profiles/matches en Fase A) —
`CoreApplications` levanta ApplicationsUnsupportedError en TODAS las
operaciones. Es la cota del contrato vigente, fijada por los contract tests
(patron search/stats de catalogo); cuando el core publique endpoints de
candidatura, se implementan en core_client.py sin tocar los routers.

CRITERIO UNIFICADOR (heredado de A.SEAM matching): mientras el escritor sea
LOCAL (hasta Fase C), ningun estado local puede ser inaccesible por el
routing. Todos los escritores REALES del estado de esta capacidad son
locales => escrituras Y lecturas se sirven de local en TODOS los modos,
incluida core_primary — nunca 501/503 por routing (seam.py).

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
- `CoreApplications` (services/applications/core_client.py): cota /v1.
La eleccion la decide `jobhunt_routing` (services/applications/seam.py).
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
    """El core no responde, fallo o no hay credencial de consumer.

    Hoy SIN emisor (CoreApplications no emite red: cota Unsupported total).
    Se conserva por simetria con catalogo/matching/profiles y para la
    separacion de severidades del canary (seam.FallbackApplications)."""


class ApplicationsUnsupportedError(ApplicationsError):
    """La operacion no existe (aun) en el contrato /v1 del core."""


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
