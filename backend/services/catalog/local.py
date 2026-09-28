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


# --- stats en una consulta (A20-12) ------------------------------------------
# Bits de GROUPING(source, canton, language, seniority, contract_type): un bit a
# 1 significa «esa columna NO agrupa en esta fila». 31 = todas a 1 = conjunto
# vacío (total); 15 = solo `source` agrupa; etc.
_STATS_TOTAL_GROUP = 31
_G_SOURCE, _G_CANTON, _G_LANGUAGE, _G_SENIORITY, _G_CONTRACT = 15, 23, 27, 29, 30
_STATS_GROUPS = {
    _G_SOURCE: "source",
    _G_CANTON: "canton",
    _G_LANGUAGE: "language",
    _G_SENIORITY: "seniority",
    _G_CONTRACT: "contract_type",
}
_STATS_SQL = """
    SELECT source, canton, language,
           CAST(seniority AS text) AS seniority,
           CAST(contract_type AS text) AS contract_type,
           GROUPING(source, canton, language, seniority, contract_type) AS g,
           count(*) AS n,
           min(salary_min_chf) FILTER (WHERE salary_max_chf IS NOT NULL) AS sal_min,
           max(salary_max_chf) FILTER (WHERE salary_max_chf IS NOT NULL) AS sal_max,
           avg(salary_max_chf) FILTER (WHERE salary_max_chf IS NOT NULL) AS sal_avg
    FROM jobs
    WHERE is_active AND duplicate_of IS NULL
    GROUP BY GROUPING SETS (
        (), (source), (canton), (language), (seniority), (contract_type)
    )
"""


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
        """Agregados del catálogo en UNA consulta (A20-12).

        Antes eran siete (total + cinco GROUP BY + salario), 63-109 ms medidos
        en local. `GROUPING SETS` produce los cinco desgloses y el conjunto
        vacío (total y agregados de salario) en un solo barrido; `GROUPING()`
        dice a qué desglose pertenece cada fila, y los valores NULL de cada
        columna se descartan como hacía el `IS NOT NULL` de antes.
        """
        rows = (await self._db.execute(text(_STATS_SQL))).all()
        total = 0
        desgloses: dict[int, dict[str, int]] = {g: {} for g in _STATS_GROUPS}
        salary_stats = SalaryStats()
        for row in rows:
            if row.g == _STATS_TOTAL_GROUP:
                total = row.n
                if row.sal_min is not None:
                    salary_stats = SalaryStats(
                        min=row.sal_min,
                        max=row.sal_max,
                        mean=round(float(row.sal_avg), 2) if row.sal_avg else None,
                    )
                continue
            columna = _STATS_GROUPS.get(row.g)
            if columna is None:
                continue
            valor = getattr(row, columna)
            if valor is not None:
                desgloses[row.g][str(valor)] = row.n

        return JobStats(
            total_jobs=total,
            by_source=desgloses[_G_SOURCE],
            by_canton=desgloses[_G_CANTON],
            by_language=desgloses[_G_LANGUAGE],
            by_seniority=desgloses[_G_SENIORITY],
            by_contract=desgloses[_G_CONTRACT],
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
