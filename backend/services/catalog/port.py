"""Read port for the catalog owned by Core.

The HTTP router keeps request validation while CoreCatalog implements search
and detail access. Aggregate operations remain explicit unsupported methods.
"""

from dataclasses import dataclass
from typing import Protocol

from schemas.job import JobSearchResponse, JobStats, SourceInfo


class CatalogError(Exception):
    """Base de errores de la capacidad catalogo."""


class CoreUnavailableError(CatalogError):
    """El core no responde, fallo o no hay credencial de consumer."""


class CatalogUnsupportedError(CatalogError):
    """La operacion no existe (aun) en el contrato /v1 del core."""


@dataclass(frozen=True)
class CatalogSearchParams:
    """Parametros de busqueda del catalogo (espejo 1:1 de la query del
    endpoint /api/v1/jobs/search; la validacion sigue en el router)."""

    q: str | None = None
    source: str | None = None
    remote_only: bool = False
    canton: str | None = None
    language: str | None = None
    seniority: str | None = None
    contract_type: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    sort: str = "newest"
    limit: int = 20
    offset: int = 0


class CatalogPort(Protocol):
    """Operaciones de LECTURA del catalogo de ofertas."""

    async def search(self, params: CatalogSearchParams) -> JobSearchResponse:
        """Busqueda paginada con filtros estructurados + full-text."""
        ...

    async def stats(self) -> JobStats:
        """Estadisticas agregadas del catalogo activo."""
        ...

    async def sources(self) -> list[SourceInfo]:
        """Fuentes activas con recuentos."""
        ...

    async def get(self, job_ref: str):
        """Detalle por referencia (hash MD5 legacy / UUID de vacante core).

        Devuelve un objeto validable como `JobResponse` o None si no existe
        (el router traduce None a 404).
        """
        ...
