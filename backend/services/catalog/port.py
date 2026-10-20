"""Read port for the catalog owned by Core.

The HTTP router keeps request validation while CoreCatalog implements search,
detail and «lo nuevo de una categoria» (`recent`, para los avisos).

Las operaciones AGREGADAS (estadisticas y fuentes con recuento) se retiraron en
DT-148 A11: el /v1 no las expone, el cliente devolvia 501 incondicional y los
dos endpoints publicos no podian servir nada. Cuando el nucleo las publique,
vuelven con su contrato, no como una cota.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from schemas.job import JobResponse, JobSearchResponse


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

    async def get(self, job_ref: str):
        """Detalle por referencia (hash MD5 legacy / UUID de vacante core).

        Devuelve un objeto validable como `JobResponse` o None si no existe
        (el router traduce None a 404).
        """
        ...

    async def recent(
        self, *, category: str, since: datetime, limit: int = 500
    ) -> list[JobResponse]:
        """Ofertas ACTIVAS de una categoria dadas de alta desde `since`.

        Existe para los avisos por correo, que necesitan «lo nuevo de una
        categoria» y no una pagina ordenada por relevancia: hasta DT-148 A1 lo
        leian de la tabla legacy `jobs`, que ya no tiene ningun escritor.
        """
        ...
