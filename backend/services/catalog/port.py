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
    # DT-148 A18-01 (2026-10-21): aquí vivían `canton`, `language`, `seniority`,
    # `contract_type` y `salary_min/max`. Los seis acababan en 501 porque el
    # content canónico no los modela, y se midió cuánto: sobre 5.000 vacantes
    # ACTIVAS de producción, `canton` 9,8 %, `language` 2,9 %,
    # `contract_type` 2,1 %, `seniority` 0,9 %, `salary_min/max` 1,1 %. Y en lo
    # cosechado los últimos 14 días es PEOR: canton 8,5 %, los otros 0-0,04 %.
    # Implementarlos habría servido filtros que esconden el 91-100 % del
    # corpus, que es peor que el 501: un error se ve, un 0 % se cree.
    # `location` SÍ está en el 100 % de las canónicas y el `/v1` lo filtra
    # (substring ci), así que la búsqueda conserva filtro de ubicación.
    location: str | None = None
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
