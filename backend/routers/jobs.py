"""Job search, detail, stats, and sources endpoints.

A.SEAM (plan §15bis): estos endpoints consumen la capacidad CATALOGO a traves
de la costura (services/catalog) — la implementacion (local|core) la decide
`jobhunt_routing` por perfil+capacidad, con default 'local'. Con routing
'local' el comportamiento es byte-identico al previo (la logica vive movida
verbatim en services/catalog/local.py). La validacion HTTP (Query) sigue aqui.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas.job import (
    JobResponse,
    JobSearchResponse,
    JobStats,
    SourceInfo,
)
from services.catalog import (
    CatalogError,
    CatalogSearchParams,
    CatalogUnsupportedError,
    CoreUnavailableError,
    resolve_catalog,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _catalog_http_error(exc: CatalogError) -> HTTPException:
    """Traduce errores de la costura a HTTP (solo alcanzable con routing a core)."""
    if isinstance(exc, CoreUnavailableError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job catalog temporarily unavailable",
        )
    if isinstance(exc, CatalogUnsupportedError):
        return HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Operation not available on the active catalog backend",
        )
    raise exc  # error de programacion: no enmascarar como HTTP


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
    catalog = await resolve_catalog(db)
    params = CatalogSearchParams(
        q=q,
        source=source,
        remote_only=remote_only,
        canton=canton,
        language=language,
        seniority=seniority,
        contract_type=contract_type,
        salary_min=salary_min,
        salary_max=salary_max,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    try:
        return await catalog.search(params)
    except (CoreUnavailableError, CatalogUnsupportedError) as exc:
        raise _catalog_http_error(exc) from exc


@router.get("/stats", response_model=JobStats)
async def get_job_stats(db: AsyncSession = Depends(get_db)):
    """Aggregated job statistics by source, canton, language, etc."""
    catalog = await resolve_catalog(db)
    try:
        return await catalog.stats()
    except (CoreUnavailableError, CatalogUnsupportedError) as exc:
        raise _catalog_http_error(exc) from exc


@router.get("/sources", response_model=list[SourceInfo])
async def get_job_sources(db: AsyncSession = Depends(get_db)):
    """List active job sources with counts."""
    catalog = await resolve_catalog(db)
    try:
        return await catalog.sources()
    except (CoreUnavailableError, CatalogUnsupportedError) as exc:
        raise _catalog_http_error(exc) from exc


@router.get("/{hash}", response_model=JobResponse)
async def get_job(hash: str, db: AsyncSession = Depends(get_db)):
    """Get full job details by hash (MD5 legacy o UUID de vacante del core)."""
    catalog = await resolve_catalog(db)
    try:
        job = await catalog.get(hash)
    except (CoreUnavailableError, CatalogUnsupportedError) as exc:
        raise _catalog_http_error(exc) from exc
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    return job
