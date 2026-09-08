"""Puerto de la capacidad DOCUMENTOS — A.SEAM (plan §15bis).

Subinterfaz POR CAPACIDAD (no fachada unica `JobHunting`). Las operaciones
son las de ALMACEN de `generated_documents` que hoy consume
`routers/documents.py`: persistir el documento generado, listarlo por oferta
y borrarlo. La ORQUESTACION de la generacion (Gemini/Groq, cache Redis,
carga de perfil/oferta/match como insumos) NO es estado de esta capacidad y
sigue en el router.

La API E.1 ya expone documentos. El resolver mantiene `CoreDocuments` SIN
vincular (Unsupported) hasta ensayar la migracion y el corte E; el adaptador
HTTP vinculado se verifica aparte y no activa un escritor core por si solo.

CRITERIO UNIFICADOR (heredado de A.SEAM matching): el UNICO escritor de
`generated_documents` es LOCAL hasta el corte E => escrituras Y lecturas se
sirven de local en TODOS los modos, incluida core_primary — nunca 501/503
por routing (services/documents/seam.py).

Dos implementaciones detras del mismo puerto:
- `LocalDocuments` (services/documents/local.py): almacen actual, movido
  verbatim del router.
- `CoreDocuments` (services/documents/core_client.py): adaptador HTTP E.2.
La eleccion la decide `jobhunt_routing` (services/documents/seam.py).
"""

import uuid
from typing import Protocol

from schemas.documents import DocumentListResponse, GeneratedDocumentResponse


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
