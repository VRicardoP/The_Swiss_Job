"""Puerto de la capacidad DOCUMENTOS — A.SEAM (plan §15bis).

Subinterfaz POR CAPACIDAD (no fachada unica `JobHunting`). Las operaciones
son las de ALMACEN de `generated_documents` que hoy consume
`routers/documents.py`: persistir el documento generado, listarlo por oferta
y borrarlo. La ORQUESTACION de la generacion (Gemini/Groq, cache Redis,
carga de perfil/oferta/match como insumos) NO es estado de esta capacidad y
sigue en el router.

VARIANTE LIGERA de la costura: el /v1 del core NO expone documentos
(jobhunt_core/api/v1.py solo sirve vacancies/profiles/matches en Fase A) —
`CoreDocuments` levanta DocumentsUnsupportedError en TODAS las operaciones.
Es la cota del contrato vigente, fijada por los contract tests (patron
search/stats de catalogo).

CRITERIO UNIFICADOR (heredado de A.SEAM matching): el UNICO escritor de
`generated_documents` es LOCAL hasta Fase C => escrituras Y lecturas se
sirven de local en TODOS los modos, incluida core_primary — nunca 501/503
por routing (services/documents/seam.py).

Dos implementaciones detras del mismo puerto:
- `LocalDocuments` (services/documents/local.py): almacen actual, movido
  verbatim del router.
- `CoreDocuments` (services/documents/core_client.py): cota /v1.
La eleccion la decide `jobhunt_routing` (services/documents/seam.py).
"""

import uuid
from typing import Protocol

from schemas.documents import DocumentListResponse, GeneratedDocumentResponse


class DocumentsError(Exception):
    """Base de errores de la capacidad documentos."""


class CoreUnavailableError(DocumentsError):
    """El core no responde, fallo o no hay credencial de consumer.

    Hoy SIN emisor (CoreDocuments no emite red: cota Unsupported total). Se
    conserva por simetria con el resto de capacidades y para la separacion
    de severidades del canary (seam.FallbackDocuments)."""


class DocumentsUnsupportedError(DocumentsError):
    """La operacion no existe (aun) en el contrato /v1 del core."""


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
