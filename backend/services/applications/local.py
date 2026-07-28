"""Implementacion LOCAL de la capacidad candidaturas — A.SEAM (plan §15bis).

Codigo MOVIDO VERBATIM de routers/applications.py (CRUD + stats) y de
routers/watchlist.py (state machine: status/draft): mismas queries, mismo
orden de condiciones, misma construccion de respuesta — con routing 'local'
el comportamiento es byte-identico al previo a la costura y los tests
existentes (test_applications.py) quedan intactos como evidencia. NO cambiar
logica aqui sin contract test que lo cubra.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.enums import ApplicationStatus
from models.job import Job
from models.job_application import JobApplication
from models.match_result import MatchResult
from schemas.applications import (
    ApplicationResponse,
    ApplicationsListResponse,
    ApplicationStatsResponse,
    ApplicationUpdate,
)

from .port import ApplicationJobNotFoundError, DuplicateApplicationError


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


class LocalApplications:
    """Almacen actual: `job_applications` + state machine en `match_results`."""

    def __init__(self, db: AsyncSession):
        self._db = db

    # ── CRUD + stats de job_applications ────────────────────────────────

    async def list(
        self,
        user_id: uuid.UUID,
        status: ApplicationStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> ApplicationsListResponse:
        db = self._db
        conditions = [JobApplication.user_id == user_id]
        if status is not None:
            conditions.append(JobApplication.status == status)

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
            .where(JobApplication.user_id == user_id)
            .group_by(JobApplication.status)
        )
        status_rows = (await db.execute(status_stmt)).all()
        by_status = {row[0]: row[1] for row in status_rows}

        return ApplicationsListResponse(data=data, total=total, by_status=by_status)

    async def create(
        self, user_id: uuid.UUID, job_hash: str, notes: str | None = None
    ) -> ApplicationResponse:
        db = self._db
        # Verify job exists
        job = (
            await db.execute(select(Job).where(Job.hash == job_hash))
        ).scalar_one_or_none()
        if job is None:
            raise ApplicationJobNotFoundError("Job not found")

        # Check for duplicate
        existing = (
            await db.execute(
                select(JobApplication).where(
                    JobApplication.user_id == user_id,
                    JobApplication.job_hash == job_hash,
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise DuplicateApplicationError("Application already exists for this job")

        app = JobApplication(
            user_id=user_id,
            job_hash=job_hash,
            status=ApplicationStatus.saved,
            notes=notes,
        )
        db.add(app)
        await db.commit()
        await db.refresh(app)

        return _to_response(app, job)

    async def stats(self, user_id: uuid.UUID) -> ApplicationStatsResponse:
        db = self._db
        base = [JobApplication.user_id == user_id]

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

    async def update(
        self,
        user_id: uuid.UUID,
        application_id: uuid.UUID,
        changes: ApplicationUpdate,
    ) -> ApplicationResponse | None:
        db = self._db
        app = (
            await db.execute(
                select(JobApplication).where(
                    JobApplication.id == application_id,
                    JobApplication.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if app is None:
            return None

        update_data = changes.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(app, field, value)

        # Auto-transition: set applied_at when status changes to applied
        if changes.status == ApplicationStatus.applied and app.applied_at is None:
            app.applied_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(app)

        job = (
            await db.execute(select(Job).where(Job.hash == app.job_hash))
        ).scalar_one_or_none()

        return _to_response(app, job)

    async def delete(self, user_id: uuid.UUID, application_id: uuid.UUID) -> bool:
        db = self._db
        app = (
            await db.execute(
                select(JobApplication).where(
                    JobApplication.id == application_id,
                    JobApplication.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if app is None:
            return False

        await db.delete(app)
        await db.commit()
        return True

    # ── State machine sobre match_results ───────────────────────────────

    async def _match_row(self, user_id: uuid.UUID, job_hash: str):
        stmt = select(MatchResult).where(
            MatchResult.user_id == user_id,
            MatchResult.job_hash == job_hash,
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()

    async def set_match_status(
        self, user_id: uuid.UUID, job_hash: str, application_status: str
    ) -> bool:
        match = await self._match_row(user_id, job_hash)
        if match is None:
            return False

        match.application_status = application_status
        match.application_status_at = datetime.now(timezone.utc)
        await self._db.commit()
        return True

    async def get_match(self, user_id: uuid.UUID, job_hash: str):
        stmt = (
            select(MatchResult, Job)
            .join(Job, Job.hash == MatchResult.job_hash)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.job_hash == job_hash,
            )
        )
        row = (await self._db.execute(stmt)).one_or_none()
        return tuple(row) if row is not None else None

    async def save_draft(self, user_id: uuid.UUID, job_hash: str, draft: str) -> bool:
        match = await self._match_row(user_id, job_hash)
        if match is None:
            return False

        match.draft_letter = draft
        # Si está en "detected" o "reviewed", avanzar a "drafted"
        if match.application_status in ("detected", "reviewed"):
            match.application_status = "drafted"
            match.application_status_at = datetime.now(timezone.utc)

        await self._db.commit()
        return True

    async def get_draft(self, user_id: uuid.UUID, job_hash: str) -> str | None:
        stmt = select(MatchResult.draft_letter).where(
            MatchResult.user_id == user_id,
            MatchResult.job_hash == job_hash,
        )
        return (await self._db.execute(stmt)).scalar_one_or_none()
