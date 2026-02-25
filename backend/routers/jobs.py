"""Job search, detail, stats, and sources endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import String, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.job import Job
from schemas.job import (
    JobBrief,
    JobResponse,
    JobSearchResponse,
    JobStats,
    SalaryStats,
    SourceInfo,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


@router.get("/search", response_model=JobSearchResponse)
async def search_jobs(
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(None, max_length=200),
    source: str | None = Query(None),
    remote_only: bool = Query(False),
    canton: str | None = Query(None),
    language: str | None = Query(None),
    seniority: str | None = Query(None),
    contract_type: str | None = Query(None),
    salary_min: int | None = Query(None, ge=0),
    salary_max: int | None = Query(None, ge=0),
    sort: str = Query("newest", pattern="^(newest|oldest|salary|relevance)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Search jobs with full-text search and structured filters."""
    # Base conditions: only active, non-duplicate jobs
    conditions = [
        Job.is_active.is_(True),
        Job.duplicate_of.is_(None),
    ]

    # Full-text search via tsvector
    if q:
        conditions.append(
            text(
                "search_vector @@ plainto_tsquery('pg_catalog.simple', :q)"
            ).bindparams(q=q)
        )

    # Comma-separated multi-value filters
    if source:
        sources = [s.strip() for s in source.split(",") if s.strip()]
        if sources:
            conditions.append(Job.source.in_(sources))
    if canton:
        cantons = [c.strip().upper() for c in canton.split(",") if c.strip()]
        if cantons:
            conditions.append(Job.canton.in_(cantons))

    # Simple filters
    if remote_only:
        conditions.append(Job.remote.is_(True))
    if language:
        conditions.append(Job.language == language)
    if seniority:
        conditions.append(cast(Job.seniority, String) == seniority)
    if contract_type:
        conditions.append(cast(Job.contract_type, String) == contract_type)

    # Salary range overlap
    if salary_min is not None:
        conditions.append(Job.salary_max_chf >= salary_min)
    if salary_max is not None:
        conditions.append(Job.salary_min_chf <= salary_max)

    # Count total before pagination
    count_stmt = select(func.count()).select_from(Job).where(*conditions)
    total = (await db.execute(count_stmt)).scalar_one()

    # Sort order
    if sort == "oldest":
        order_clause = Job.first_seen_at.asc()
    elif sort == "salary":
        order_clause = Job.salary_max_chf.desc().nulls_last()
    elif sort == "relevance" and q:
        order_clause = text(
            "ts_rank(search_vector, plainto_tsquery('pg_catalog.simple', :q)) DESC"
        ).bindparams(q=q)
    else:  # newest (default)
        order_clause = Job.last_seen_at.desc()

    # Main query with pagination
    stmt = (
        select(Job)
        .where(*conditions)
        .order_by(order_clause)
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(stmt)
    jobs = result.scalars().all()

    return JobSearchResponse(
        data=[JobBrief.model_validate(j) for j in jobs],
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


@router.get("/stats", response_model=JobStats)
async def get_job_stats(db: AsyncSession = Depends(get_db)):
    """Aggregated job statistics by source, canton, language, etc."""
    base_filter = [Job.is_active.is_(True), Job.duplicate_of.is_(None)]

    # Total count
    total = (
        await db.execute(select(func.count()).select_from(Job).where(*base_filter))
    ).scalar_one()

    # Group-by helper
    async def _group_by(column):
        stmt = (
            select(column, func.count())
            .where(*base_filter)
            .where(column.is_not(None))
            .group_by(column)
        )
        rows = (await db.execute(stmt)).all()
        return {str(row[0]): row[1] for row in rows}

    by_source = await _group_by(Job.source)
    by_canton = await _group_by(Job.canton)
    by_language = await _group_by(Job.language)
    by_seniority = await _group_by(Job.seniority)
    by_contract = await _group_by(Job.contract_type)

    # Salary stats (only jobs with salary data)
    salary_stmt = (
        select(
            func.min(Job.salary_min_chf),
            func.max(Job.salary_max_chf),
            func.avg(Job.salary_max_chf),
        )
        .where(*base_filter)
        .where(Job.salary_max_chf.is_not(None))
    )
    salary_row = (await db.execute(salary_stmt)).one_or_none()

    salary_stats = SalaryStats()
    if salary_row and salary_row[0] is not None:
        salary_stats = SalaryStats(
            min=salary_row[0],
            max=salary_row[1],
            mean=round(float(salary_row[2]), 2) if salary_row[2] else None,
        )

    return JobStats(
        total_jobs=total,
        by_source=by_source,
        by_canton=by_canton,
        by_language=by_language,
        by_seniority=by_seniority,
        by_contract=by_contract,
        salary_stats=salary_stats,
    )


@router.get("/sources", response_model=list[SourceInfo])
async def get_job_sources(db: AsyncSession = Depends(get_db)):
    """List active job sources with counts."""
    stmt = (
        select(
            Job.source,
            func.count().label("count"),
            func.max(Job.last_seen_at).label("last_seen"),
        )
        .where(Job.is_active.is_(True), Job.duplicate_of.is_(None))
        .group_by(Job.source)
        .order_by(func.count().desc())
    )
    rows = (await db.execute(stmt)).all()
    return [
        SourceInfo(name=row.source, count=row.count, last_seen=row.last_seen)
        for row in rows
    ]


@router.get("/{hash}", response_model=JobResponse)
async def get_job(hash: str, db: AsyncSession = Depends(get_db)):
    """Get full job details by hash."""
    result = await db.execute(
        select(Job).where(Job.hash == hash, Job.is_active.is_(True))
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    return job
