"""School catalogue port. Local static metadata or core tenant-owned monitors.

E.15 keeps the existing watchlist payload. Routing selects the implementation;
state/preferences and production observations use their narrow companion clients.
An explicitly unbound CoreSchools instance retains the rollout Unsupported error.
"""

from typing import Protocol


class SchoolsError(Exception):
    """Base de errores de la capacidad colegios."""


class CoreUnavailableError(SchoolsError):
    """Core unavailable, misconfigured, or response violates the contract."""


class SchoolsUnsupportedError(SchoolsError):
    """La operacion no existe (aun) en el contrato /v1 del core."""


class SchoolsPort(Protocol):
    """Operacion de LECTURA del listado de colegios vigilados."""

    async def list(self) -> dict:
        """Payload {"schools": [...]} con la metadata publica de cada
        colegio (el router lo devuelve tal cual)."""
        ...
