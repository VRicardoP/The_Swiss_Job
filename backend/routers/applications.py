"""Job application tracking endpoints — CRUD + stats."""

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import get_current_user
from database import get_db
from models.enums import ApplicationStatus
from models.job import Job
from models.job_application import JobApplication
from models.user import User
from schemas.applications import (
    ApplicationCreate,
    ApplicationResponse,
    ApplicationsListResponse,
    ApplicationStatsResponse,
    ApplicationUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])


def _to_response(app: JobApplication, job: Job | None) -> ApplicationResponse:
    """Convert a JobApplication + optional Job to response schema."""
    return ApplicationResponse(
        id=app.id,
        user_id=app.user_id,
        job_hash=app.job_hash,
        status=app.status,
        notes=app.notes,
        applied_at=app.applied_at,
        applied_url=app.applied_url,
        follow_up_date=app.follow_up_date,
        created_at=app.created_at,
        updated_at=app.updated_at,
        job_title=job.title if job else None,
        job_company=job.company if job else None,
        job_location=job.location if job else None,
        job_source=job.source if job else None,
    )


@router.get("", response_model=ApplicationsListResponse)
async def list_applications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    app_status: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List user's applications with optional status filter."""
    conditions = [JobApplication.user_id == current_user.id]

    if app_status:
        try:
            parsed = ApplicationStatus(app_status)
            conditions.append(JobApplication.status == parsed)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {app_status}",
            )

    # Count total
    total = (
        await db.execute(
            select(func.count()).select_from(JobApplication).where(*conditions)
        )
    ).scalar_one()

    # Fetch applications with job join
    stmt = (
        select(JobApplication, Job)
        .outerjoin(Job, JobApplication.job_hash == Job.hash)
        .where(*conditions)
        .order_by(JobApplication.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).all()
    data = [_to_response(app, job) for app, job in rows]

    # Status summary
    status_stmt = (
        select(
            cast(JobApplication.status, String),
            func.count(),
        )
        .where(JobApplication.user_id == current_user.id)
        .group_by(JobApplication.status)
    )
    status_rows = (await db.execute(status_stmt)).all()
    by_status = {row[0]: row[1] for row in status_rows}

    return ApplicationsListResponse(data=data, total=total, by_status=by_status)


@router.post("", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
async def create_application(
    body: ApplicationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new application (status=saved)."""
    # Verify job exists
    job = (
        await db.execute(select(Job).where(Job.hash == body.job_hash))
    ).scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )

    # Check for duplicate
    existing = (
        await db.execute(
            select(JobApplication).where(
                JobApplication.user_id == current_user.id,
                JobApplication.job_hash == body.job_hash,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Application already exists for this job",
        )

    app = JobApplication(
        user_id=current_user.id,
        job_hash=body.job_hash,
        status=ApplicationStatus.saved,
        notes=body.notes,
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)

    return _to_response(app, job)


@router.get("/stats", response_model=ApplicationStatsResponse)
async def get_application_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Application pipeline statistics and conversion rates."""
    base = [JobApplication.user_id == current_user.id]

    # By status
    status_stmt = (
        select(cast(JobApplication.status, String), func.count())
        .where(*base)
        .group_by(JobApplication.status)
    )
    status_rows = (await db.execute(status_stmt)).all()
    by_status = {row[0]: row[1] for row in status_rows}

    # By source (join with Job)
    source_stmt = (
        select(Job.source, func.count())
        .select_from(JobApplication)
        .join(Job, JobApplication.job_hash == Job.hash)
        .where(*base)
        .group_by(Job.source)
    )
    source_rows = (await db.execute(source_stmt)).all()
    by_source = {row[0]: row[1] for row in source_rows}

    # Conversion rates
    total = sum(by_status.values())
    conversion_rates = {}
    if total > 0:
        applied = sum(
            v for k, v in by_status.items() if k != ApplicationStatus.saved.value
        )
        conversion_rates["saved_to_applied"] = round(applied / total, 3)

        interviews = sum(
            v
            for k, v in by_status.items()
            if k
            in {
                ApplicationStatus.interview.value,
                ApplicationStatus.offer.value,
            }
        )
        if applied > 0:
            conversion_rates["applied_to_interview"] = round(
                interviews / applied, 3
            )

        offers = by_status.get(ApplicationStatus.offer.value, 0)
        if interviews > 0:
            conversion_rates["interview_to_offer"] = round(offers / interviews, 3)

    return ApplicationStatsResponse(
        by_status=by_status,
        conversion_rates=conversion_rates,
        by_source=by_source,
    )


@router.patch("/{application_id}", response_model=ApplicationResponse)
async def update_application(
    application_id: uuid.UUID,
    body: ApplicationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update application status, notes, or follow-up date."""
    app = (
        await db.execute(
            select(JobApplication).where(
                JobApplication.id == application_id,
                JobApplication.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(app, field, value)

    # Auto-transition: set applied_at when status changes to applied
    if body.status == ApplicationStatus.applied and app.applied_at is None:
        app.applied_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(app)

    job = (
        await db.execute(select(Job).where(Job.hash == app.job_hash))
    ).scalar_one_or_none()

    return _to_response(app, job)


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application(
    application_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an application."""
    app = (
        await db.execute(
            select(JobApplication).where(
                JobApplication.id == application_id,
                JobApplication.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if app is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application not found",
        )

    await db.delete(app)
    await db.commit()
