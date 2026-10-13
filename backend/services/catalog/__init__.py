"""Core catalogue authority."""

from .core_client import CoreCatalog
from .port import (
    CatalogError,
    CatalogPort,
    CatalogSearchParams,
    CatalogUnsupportedError,
    CoreUnavailableError,
)
from .seam import resolve_catalog

__all__ = [
    "CatalogError",
    "CatalogPort",
    "CatalogSearchParams",
    "CatalogUnsupportedError",
    "CoreCatalog",
    "CoreUnavailableError",
    "resolve_catalog",
]
