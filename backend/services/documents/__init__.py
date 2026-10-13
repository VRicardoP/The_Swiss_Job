"""Core document authority."""

from .core_client import CoreDocuments
from .port import (
    CoreUnavailableError,
    DocumentsError,
    DocumentsPort,
    DocumentsUnsupportedError,
)
from .seam import resolve_documents

__all__ = [
    "CoreDocuments",
    "CoreUnavailableError",
    "DocumentsError",
    "DocumentsPort",
    "DocumentsUnsupportedError",
    "resolve_documents",
]
