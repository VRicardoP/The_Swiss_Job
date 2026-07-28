"""Implementacion CORE de la capacidad documentos — A.SEAM (plan §15bis).

Contrato REAL del /v1 (jobhunt_core/api/v1.py, Fase A): solo expone
GET /vacancies/{id}, GET /profiles/{id} y GET /profiles/{id}/matches. NO hay
endpoints de documentos generados: TODAS las operaciones del puerto levantan
DocumentsUnsupportedError.

Es la cota del contrato vigente, fijada por los contract tests (patron
search/stats de catalogo): esta clase no abre cliente HTTP ni necesita
credencial — CERO peticiones por construccion. Cuando el core publique sus
endpoints de documentos, se implementan aqui sin tocar los routers.
"""

from .port import DocumentsUnsupportedError

_UNSUPPORTED_MSG = "el /v1 del core no expone la capacidad de documentos"


class CoreDocuments:
    """Cota /v1 detras del puerto DocumentsPort (sin red, sin credencial)."""

    async def create(
        self,
        user_id,
        job_hash,
        doc_type,
        content,
        language,
        job_title=None,
        job_company=None,
    ):
        raise DocumentsUnsupportedError(_UNSUPPORTED_MSG)

    async def list(self, user_id, job_hash, doc_type=None):
        raise DocumentsUnsupportedError(_UNSUPPORTED_MSG)

    async def delete(self, user_id, document_id):
        raise DocumentsUnsupportedError(_UNSUPPORTED_MSG)
