"""Anonymous Core catalog endpoints for search and detail.

The router owns HTTP validation; the fixed Core adapter owns catalog access.
The catalog stays public by product design, while user data remains authenticated.

DT-148 A11: los agregados (`/stats`, `/sources`) se retiraron. El /v1 no los
expone, así que devolvían 501 SIEMPRE: el desplegable de fuentes del buscador
estaba vacío desde el corte y nadie lo veía porque el front se tragaba el error.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas.job import JobResponse, JobSearchResponse
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
    location: str | None = Query(None, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Búsqueda full-text con los filtros que el catálogo SÍ sirve.

    DT-148 A18-01 (2026-10-21): este endpoint aceptaba además `canton`,
    `language`, `seniority`, `contract_type`, `salary_min/max` y tres órdenes
    (`oldest`, `salary`, `relevance`). Los nueve devolvían **501**: el content
    canónico no modela esos campos y el `/v1` sólo ordena por `newest`. Se
    retiran en vez de implementarse porque la medición lo desaconseja — sobre
    5.000 vacantes activas de producción, la cobertura va del 9,8 % de `canton`
    al 0 % de `seniority`, y en lo cosechado los últimos 14 días es peor. Un
    filtro que esconde el 91-100 % del corpus miente peor que un 501.

    `location` entra en su lugar: el `/v1` lo filtra como substring sobre el
    `location` de la canónica, que está en el 100 % de las ofertas.
    """
    catalog = await resolve_catalog(db)
    params = CatalogSearchParams(
        q=q,
        source=source,
        remote_only=remote_only,
        location=location,
        limit=limit,
        offset=offset,
    )
    try:
        return await catalog.search(params)
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
