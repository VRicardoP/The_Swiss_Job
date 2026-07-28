"""Costura de la capacidad documentos — A.SEAM (plan §15bis).

Resuelve QUE implementacion sirve cada peticion segun `jobhunt_routing`
(default 'local'). Mapeo modo -> implementacion, derivado de la matriz de
escritor por estado del plan §15bis Y del criterio unificador (heredado de
A.SEAM matching: ningun estado local puede ser inaccesible por el routing):

- local / shadow           -> LocalDocuments (el legacy es el motor)
- core_read                -> FallbackDocuments (canary: intenta el core y
                              cae a local; hoy TODA operacion cae — la cota
                              /v1 es Unsupported total y se registra a DEBUG,
                              severidades del canary heredadas)
- core_primary / rollback_pending -> LocalDocuments. CRITERIO UNIFICADOR: el
                              UNICO escritor de `generated_documents` es
                              LOCAL hasta Fase C y el /v1 no expone la
                              capacidad — el estado del escritor local es
                              SIEMPRE accesible; enrutar al core seria 501
                              para estado que solo existe aqui.

Como en matching/profiles, la resolucion es POR PERFIL:
`jobhunt_routing.profile_id` para SwissJob es `users.id`.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from services.routing import CAPABILITY_DOCUMENTS, MODE_CORE_READ, resolve_mode

from .core_client import CoreDocuments
from .local import LocalDocuments
from .port import CoreUnavailableError, DocumentsPort, DocumentsUnsupportedError

logger = logging.getLogger(__name__)


class FallbackDocuments:
    """Canary (core_read): intenta el core y cae al local.

    Hoy la cota /v1 es Unsupported TOTAL: toda operacion cae a local a ritmo
    de trafico (DEBUG, esperado por contrato). Se conserva la estructura del
    canary para que, cuando el core publique endpoints de documentos, la
    unica senal WARNING siga siendo CoreUnavailableError (core caido)."""

    def __init__(self, primary: DocumentsPort, fallback: DocumentsPort):
        self._primary = primary
        self._fallback = fallback

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
        try:
            return await self._primary.create(
                user_id,
                job_hash,
                doc_type,
                content,
                language,
                job_title=job_title,
                job_company=job_company,
            )
        except (CoreUnavailableError, DocumentsUnsupportedError) as exc:
            self._warn("create", exc)
            return await self._fallback.create(
                user_id,
                job_hash,
                doc_type,
                content,
                language,
                job_title=job_title,
                job_company=job_company,
            )

    async def list(self, user_id, job_hash, doc_type=None):
        try:
            return await self._primary.list(user_id, job_hash, doc_type=doc_type)
        except (CoreUnavailableError, DocumentsUnsupportedError) as exc:
            self._warn("list", exc)
            return await self._fallback.list(user_id, job_hash, doc_type=doc_type)

    async def delete(self, user_id, document_id):
        try:
            return await self._primary.delete(user_id, document_id)
        except (CoreUnavailableError, DocumentsUnsupportedError) as exc:
            self._warn("delete", exc)
            return await self._fallback.delete(user_id, document_id)

    @staticmethod
    def _warn(op: str, exc: Exception) -> None:
        # Severidades separadas (2ª rev. A.SEAM catalogo, misma regla): el
        # fallback por Unsupported es ESPERADO por contrato y ocurre a ritmo
        # de trafico — a WARNING ahogaria la UNICA senal accionable del
        # canary (CoreUnavailableError = core caido o mal configurado).
        if isinstance(exc, DocumentsUnsupportedError):
            logger.debug(
                "documentos core_read: %s cayo a local (cota /v1: %s)", op, exc
            )
        else:
            logger.warning("documentos core_read: %s cayo a local (%s)", op, exc)


async def resolve_documents(
    db: AsyncSession, user_id: uuid.UUID | None = None
) -> DocumentsPort:
    """Puerto de documentos para esta peticion segun el routing por perfil."""
    mode = await resolve_mode(db, CAPABILITY_DOCUMENTS, user_id)
    if mode == MODE_CORE_READ:
        return FallbackDocuments(CoreDocuments(), LocalDocuments(db))
    # local / shadow: el legacy es el motor. core_primary / rollback_pending:
    # criterio unificador — el escritor de este estado es local UNICO y el
    # /v1 no expone la capacidad => local, nunca 501/503 (docstring modulo).
    return LocalDocuments(db)
