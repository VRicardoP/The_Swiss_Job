"""Capacidad DOCUMENTOS detras de costura — A.SEAM (plan §15bis).

API publica del paquete: el puerto, sus errores y el resolver que decide
implementacion (local|core) via jobhunt_routing.
"""

from .core_client import CoreDocuments
from .local import LocalDocuments
from .port import (
    CoreUnavailableError,
    DocumentsError,
    DocumentsPort,
    DocumentsUnsupportedError,
)
from .seam import FallbackDocuments, resolve_documents

__all__ = [
    "CoreDocuments",
    "CoreUnavailableError",
    "DocumentsError",
    "DocumentsPort",
    "DocumentsUnsupportedError",
    "FallbackDocuments",
    "LocalDocuments",
    "resolve_documents",
]
