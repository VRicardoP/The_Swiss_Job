"""Capacidad CATALOGO detras de costura — A.SEAM (plan §15bis).

API publica del paquete: el puerto, sus errores/parametros y el resolver
que decide implementacion (local|core) via jobhunt_routing.
"""

from .core_client import CoreCatalog
from .local import LocalCatalog
from .port import (
    CatalogError,
    CatalogPort,
    CatalogSearchParams,
    CatalogUnsupportedError,
    CoreUnavailableError,
)
from .seam import FallbackCatalog, resolve_catalog

__all__ = [
    "CatalogError",
    "CatalogPort",
    "CatalogSearchParams",
    "CatalogUnsupportedError",
    "CoreCatalog",
    "CoreUnavailableError",
    "FallbackCatalog",
    "LocalCatalog",
    "resolve_catalog",
]
