"""Job application tracking endpoints — CRUD + stats.

A.SEAM (plan §15bis) + escrituras C-4: estos endpoints consumen la capacidad
CANDIDATURAS a traves de la costura (services/applications) — la
implementacion la decide `jobhunt_routing` por perfil+capacidad, con default
'local'. Con routing 'local' el comportamiento es byte-identico al previo (la
logica vive movida verbatim en services/applications/local.py). Con routing
core_primary/rollback_pending el CRUD lo sirve el cliente C-4 del core SIN
fallback silencioso: CoreUnavailableError → 503 y
ApplicationsUnsupportedError (cotas del contrato, p.ej. applied_url) → 501
honestos, nunca una escritura local que bifurque el estado. La validacion
HTTP sigue aqui.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import get_current_user
from database import get_db
from models.enums import ApplicationStatus
from models.user import User
from schemas.applications import (
    ApplicationCreate,
    ApplicationResponse,
    ApplicationsListResponse,
    ApplicationStatsResponse,
    ApplicationUpdate,
)
from services.applications import (
    ApplicationJobNotFoundError,
    ApplicationsError,
    ApplicationsUnsupportedError,
    CoreUnavailableError,
    DuplicateApplicationError,
    resolve_applications,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])


def _core_http_error(exc: ApplicationsError) -> HTTPException:
    """Traduce errores del backend core a HTTP (solo alcanzable con routing
    core_primary/rollback_pending — mismo patron que routers/jobs.py)."""
    if isinstance(exc, CoreUnavailableError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Applications backend temporarily unavailable",
        )
    if isinstance(exc, ApplicationsUnsupportedError):
        return HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Operation not available on the active applications backend",
        )
    raise exc  # error de programacion: no enmascarar como HTTP


@router.get("", response_model=ApplicationsListResponse)
async def list_applications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    app_status: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List user's applications with optional status filter."""
    parsed: ApplicationStatus | None = None
    if app_status:
        try:
            parsed = ApplicationStatus(app_status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {app_status}",
            )

    applications = await resolve_applications(db, current_user.id)
    try:
        return await applications.list(
            current_user.id, status=parsed, limit=limit, offset=offset
        )
    except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
        raise _core_http_error(exc)


@router.post(
    "", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED
)
async def create_application(
    body: ApplicationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new application (status=saved)."""
    applications = await resolve_applications(db, current_user.id)
    try:
        return await applications.create(
            current_user.id, body.job_hash, notes=body.notes
        )
    except ApplicationJobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    except DuplicateApplicationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Application already exists for this job",
        )
    except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
        raise _core_http_error(exc)


@router.get("/stats", response_model=ApplicationStatsResponse)
async def get_application_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Application pipeline statistics and conversion rates."""
    applications = await resolve_applications(db, current_user.id)
    try:
        return await applications.stats(current_user.id)
    except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
        raise _core_http_error(exc)


@router.patch("/{application_id}", response_model=ApplicationResponse)
async def update_application(
    application_id: uuid.UUID,
    body: ApplicationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update application status, notes, or follow-up date."""
    applications = await resolve_applications(db, current_user.id)
    try:
        updated = await applications.update(current_user.id, application_id, body)
    except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
        raise _core_http_error(exc)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )
    return updated


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an application."""
    applications = await resolve_applications(db, current_user.id)
    try:
        deleted = await applications.delete(current_user.id, application_id)
    except (CoreUnavailableError, ApplicationsUnsupportedError) as exc:
        raise _core_http_error(exc)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )
