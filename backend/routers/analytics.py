"""Router de analytics — sugerencias de patrones y filtros de exclusión de usuario."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import get_current_user
from database import get_db
from models.job_filter import JobFilter, PatternSuggestion
from models.user import User
from schemas.analytics import (
    AnalyzeRejectedRequest,
    AnalyzeRejectedResponse,
    CreateFilterRequest,
    JobFilterResponse,
    JobFiltersResponse,
    PatternSuggestionsResponse,
    ReviewSuggestionRequest,
    ReviewSuggestionResponse,
)
from services.pattern_analysis_service import PatternAnalysisService
from services.matching.port import CoreUnavailableError

from services.exclusions_sync import (
    exclusion_sync_status,
    lock_filter_writer,
    queue_exclusions,
    sync_exclusions_to_core,
)

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


# ---------------------------------------------------------------------------
# Sugerencias de patrones
# ---------------------------------------------------------------------------


@router.post("/analyze", response_model=AnalyzeRejectedResponse)
async def analyze_rejected_jobs(
    body: AnalyzeRejectedRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Analiza los jobs rechazados y genera sugerencias de filtros.

    Elimina sugerencias pendientes anteriores y genera nuevas.
    Las sugerencias aprobadas o rechazadas no se tocan.
    """
    service = PatternAnalysisService(db)
    try:
        context = await service.load_jobs(current_user.id)
        rejected_count = len(context[0])
        generated = await service.analyze_and_generate(
            user_id=current_user.id,
            min_rejected=body.min_rejected,
            job_context=context,
        )
    except CoreUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Historial de feedback no disponible"
        ) from exc
    return AnalyzeRejectedResponse(
        status="success",
        suggestions_generated=generated,
        rejected_jobs_analyzed=rejected_count,
    )


@router.get("/suggestions", response_model=PatternSuggestionsResponse)
async def list_suggestions(
    status_filter: str = "pending",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista sugerencias del usuario. Por defecto solo las pendientes."""
    valid_statuses = {"pending", "approved", "rejected", "all"}
    if status_filter not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status_filter debe ser uno de: {', '.join(valid_statuses)}",
        )

    stmt = select(PatternSuggestion).where(PatternSuggestion.user_id == current_user.id)
    if status_filter != "all":
        stmt = stmt.where(PatternSuggestion.status == status_filter)
    stmt = stmt.order_by(PatternSuggestion.confidence.desc())

    result = await db.execute(stmt)
    suggestions = result.scalars().all()

    return PatternSuggestionsResponse(data=list(suggestions), total=len(suggestions))


@router.post(
    "/suggestions/{suggestion_id}/review",
    response_model=ReviewSuggestionResponse,
)
async def review_suggestion(
    suggestion_id: uuid.UUID,
    body: ReviewSuggestionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aprueba o rechaza una sugerencia de patrón.

    Si se aprueba, crea un JobFilter activo con el patrón sugerido.
    """
    await lock_filter_writer(db, current_user.id)
    result = await db.execute(
        select(PatternSuggestion).where(
            PatternSuggestion.id == suggestion_id,
            PatternSuggestion.user_id == current_user.id,
        )
    )
    suggestion = result.scalar_one_or_none()
    if suggestion is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Sugerencia no encontrada"
        )

    if suggestion.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"La sugerencia ya fue revisada: {suggestion.status}",
        )

    now = datetime.now(timezone.utc)
    # body.action es el verbo ("approve"/"reject"); el status persistido usa el
    # participio ("approved"/"rejected"), que es el vocabulario con el que
    # list_suggestions filtra. Sin este mapeo, una sugerencia revisada quedaba
    # invisible ante ?status_filter=approved.
    suggestion.status = "approved" if body.action == "approve" else "rejected"
    suggestion.reviewed_at = now

    filter_id: uuid.UUID | None = None

    if body.action == "approve":
        filter_type = (
            "tag_contains"
            if suggestion.suggestion_type == "tag_category"
            else "title_contains"
        )
        new_filter = JobFilter(
            user_id=current_user.id,
            filter_type=filter_type,
            pattern=suggestion.pattern,
            description=suggestion.description,
            source="auto",
            approved_at=now,
        )
        db.add(new_filter)
        await db.flush()
        filter_id = new_filter.id

    if body.action == "approve":
        await queue_exclusions(db, current_user.id)
    await db.commit()
    if body.action == "approve":
        # La aprobación crea una regla efectiva: mismo camino declarativo.
        await sync_exclusions_to_core(db, current_user.id)

    return ReviewSuggestionResponse(
        status="success",
        suggestion_id=suggestion_id,
        filter_id=filter_id,
    )


# ---------------------------------------------------------------------------
# Filtros activos
# ---------------------------------------------------------------------------


@router.get("/filters", response_model=JobFiltersResponse)
async def list_filters(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lista los filtros de exclusión activos del usuario."""
    stmt = (
        select(JobFilter)
        .where(
            JobFilter.user_id == current_user.id,
            JobFilter.is_active.is_(True),
        )
        .order_by(JobFilter.created_at.desc())
    )
    result = await db.execute(stmt)
    filters = result.scalars().all()

    total_stmt = (
        select(func.count())
        .select_from(JobFilter)
        .where(
            JobFilter.user_id == current_user.id,
            JobFilter.is_active.is_(True),
        )
    )
    total = (await db.execute(total_stmt)).scalar_one()

    return JobFiltersResponse(
        data=list(filters),
        total=total,
        sync_status=await exclusion_sync_status(db, current_user.id),
    )


@router.post(
    "/filters",
    response_model=JobFilterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_filter(
    body: CreateFilterRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Crea un filtro de exclusión manual."""
    now = datetime.now(timezone.utc)
    new_filter = JobFilter(
        user_id=current_user.id,
        filter_type=body.filter_type,
        pattern=body.pattern.lower().strip(),
        description=body.description,
        source="manual",
        approved_at=now,
    )
    await lock_filter_writer(db, current_user.id)
    db.add(new_filter)
    await queue_exclusions(db, current_user.id)
    await db.commit()
    await db.refresh(new_filter)
    # El core sirve el feed y aplica estas reglas: se le empuja el conjunto
    # COMPLETO (declarativo) para que altas y bajas no puedan perderse
    # (revisión externa 2026-09-07, hallazgo B).
    await sync_exclusions_to_core(db, current_user.id)
    return new_filter


@router.delete("/filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filter(
    filter_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Elimina (desactiva) un filtro de exclusión."""
    await lock_filter_writer(db, current_user.id)
    result = await db.execute(
        select(JobFilter).where(
            JobFilter.id == filter_id,
            JobFilter.user_id == current_user.id,
        )
    )
    job_filter = result.scalar_one_or_none()
    if job_filter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Filtro no encontrado"
        )

    job_filter.is_active = False
    await queue_exclusions(db, current_user.id)
    await db.commit()
    # BAJA: la proyección declarativa la lleva al core; sin esto la regla
    # seguía excluyendo allí para siempre (hallazgo B).
    await sync_exclusions_to_core(db, current_user.id)
