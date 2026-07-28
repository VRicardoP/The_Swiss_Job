"""Implementacion LOCAL de la capacidad catalogo — A.SEAM (plan §15bis).

Codigo MOVIDO VERBATIM de routers/jobs.py (mismas queries, mismo orden de
condiciones, misma construccion de respuesta): con routing 'local' el
comportamiento es byte-identico al previo a la costura. NO cambiar logica
aqui sin contract test que lo cubra.
"""

from sqlalchemy import String, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from schemas.job import (
    JobBrief,
    JobSearchResponse,
    JobStats,
    SalaryStats,
    SourceInfo,
)

from .port import CatalogSearchParams


class LocalCatalog:
    """Motor actual: lecturas directas sobre la tabla `jobs`."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def search(self, params: CatalogSearchParams) -> JobSearchResponse:
        db = self._db
        # Base conditions: only active, non-duplicate, non-student jobs
        conditions = [
            Job.is_active.is_(True),
            Job.duplicate_of.is_(None),
            *Job.exclude_student_conditions(),
        ]

        # Full-text search via tsvector
        if params.q:
            conditions.append(
                text(
                    "search_vector @@ plainto_tsquery('pg_catalog.simple', :q)"
                ).bindparams(q=params.q)
            )

        # Comma-separated multi-value filters
        if params.source:
            sources = [s.strip() for s in params.source.split(",") if s.strip()]
            if sources:
                conditions.append(Job.source.in_(sources))
        if params.canton:
            cantons = [c.strip().upper() for c in params.canton.split(",") if c.strip()]
            if cantons:
                conditions.append(Job.canton.in_(cantons))

        # Simple filters
        if params.remote_only:
            conditions.append(Job.remote.is_(True))
        if params.language:
            conditions.append(Job.language == params.language)
        if params.seniority:
            conditions.append(cast(Job.seniority, String) == params.seniority)
        if params.contract_type:
            conditions.append(cast(Job.contract_type, String) == params.contract_type)

        # Salary range overlap
        if params.salary_min is not None:
            conditions.append(Job.salary_max_chf >= params.salary_min)
        if params.salary_max is not None:
            conditions.append(Job.salary_min_chf <= params.salary_max)

        # Count total before pagination
        count_stmt = select(func.count()).select_from(Job).where(*conditions)
        total = (await db.execute(count_stmt)).scalar_one()

        # Sort order
        if params.sort == "oldest":
            order_clause = Job.first_seen_at.asc()
        elif params.sort == "salary":
            order_clause = Job.salary_max_chf.desc().nulls_last()
        elif params.sort == "relevance" and params.q:
            order_clause = text(
                "ts_rank(search_vector, plainto_tsquery('pg_catalog.simple', :q)) DESC"
            ).bindparams(q=params.q)
        else:  # newest (default)
            order_clause = Job.last_seen_at.desc()

        # Main query with pagination
        stmt = (
            select(Job)
            .where(*conditions)
            .order_by(order_clause)
            .limit(params.limit)
            .offset(params.offset)
        )

        result = await db.execute(stmt)
        jobs = result.scalars().all()

        return JobSearchResponse(
            data=[JobBrief.model_validate(j) for j in jobs],
            total=total,
            limit=params.limit,
            offset=params.offset,
            has_more=(params.offset + params.limit) < total,
        )

    async def stats(self) -> JobStats:
        db = self._db
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

    async def sources(self) -> list[SourceInfo]:
        db = self._db
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

    async def get(self, job_ref: str):
        result = await self._db.execute(
            select(Job).where(Job.hash == job_ref, Job.is_active.is_(True))
        )
        # Devuelve el ORM Job (como hacia el router): response_model lo valida.
        return result.scalar_one_or_none()
