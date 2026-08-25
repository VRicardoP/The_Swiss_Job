"""Capacidad CANDIDATURAS detras de costura — A.SEAM (plan §15bis).

API publica del paquete: el puerto, sus errores y el resolver que decide
implementacion (local|core) via jobhunt_routing.
"""

from .core_client import CoreApplications
from .local import LocalApplications
from .port import (
    ApplicationJobNotFoundError,
    ApplicationsError,
    ApplicationsPort,
    ApplicationsUnsupportedError,
    CoreUnavailableError,
    DuplicateApplicationError,
)
from .seam import CoreWriterApplications, resolve_applications

__all__ = [
    "ApplicationJobNotFoundError",
    "ApplicationsError",
    "ApplicationsPort",
    "ApplicationsUnsupportedError",
    "CoreApplications",
    "CoreUnavailableError",
    "CoreWriterApplications",
    "DuplicateApplicationError",
    "LocalApplications",
    "resolve_applications",
]
