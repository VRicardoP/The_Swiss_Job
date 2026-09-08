"""Document storage port shared by local and core authorities.

Generation and prepared delivery are orchestrated outside this storage interface.
Routing selects one writer; core failures never cause a second local write.
"""

import uuid
from typing import Protocol

from schemas.documents import DocumentListResponse, GeneratedDocumentResponse, DocumentPageResponse


class DocumentsError(Exception):
    """Base de errores de la capacidad documentos."""


class CoreUnavailableError(DocumentsError):
    """Core inaccesible, respuesta incompatible o vinculacion incompleta."""


class DocumentsUnsupportedError(DocumentsError):
    """Operacion no habilitada: el adaptador sigue sin vincular al core."""


class DocumentsPort(Protocol):
    """Operaciones de almacen de documentos generados de un usuario."""

    async def create(
        self,
        user_id: uuid.UUID,
        job_hash: str,
        doc_type: str,
        content: str,
        language: str,
        job_title: str | None = None,
        job_company: str | None = None,
    ) -> GeneratedDocumentResponse:
        """Persiste un documento ya generado y devuelve su representacion."""
        ...

    async def list(
        self, user_id: uuid.UUID, job_hash: str, doc_type: str | None = None
    ) -> DocumentListResponse:
        """Documentos del usuario para una oferta, orden created_at DESC."""
        ...

    async def delete(self, user_id: uuid.UUID, document_id: uuid.UUID) -> bool:
        """Borrado; False si no existe para ese usuario (=> 404)."""
        ...
