"""Capacidad COLEGIOS detras de costura — A.SEAM (plan §15bis).

API publica del paquete: el puerto, sus errores y el resolver que decide
implementacion (local|core) via jobhunt_routing.
"""

from .core_client import CoreSchools
from .local import LocalSchools
from .port import (
    CoreUnavailableError,
    SchoolsError,
    SchoolsPort,
    SchoolsUnsupportedError,
)
from .seam import FallbackSchools, resolve_schools

__all__ = [
    "CoreSchools",
    "CoreUnavailableError",
    "FallbackSchools",
    "LocalSchools",
    "SchoolsError",
    "SchoolsPort",
    "SchoolsUnsupportedError",
    "resolve_schools",
]
