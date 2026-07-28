"""Puerto de la capacidad CATALOGO — A.SEAM (plan §15bis).

Subinterfaz POR CAPACIDAD (no fachada unica `JobHunting`). Las operaciones
son exactamente las que hoy consumen los routers de jobs/busqueda
(`routers/jobs.py`): busqueda con filtros, estadisticas, fuentes y detalle.

Dos implementaciones detras del mismo puerto:
- `LocalCatalog` (services/catalog/local.py): motor actual, sin cambios.
- `CoreCatalog` (services/catalog/core_client.py): cliente HTTP del /v1 del core.
La eleccion la decide `jobhunt_routing` (services/catalog/seam.py).
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
