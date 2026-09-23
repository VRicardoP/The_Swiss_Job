"""Document authority after the E cutover contract.

local/shadow/core_read keep the local writer. core_primary/rollback_pending use
only core, with no fallback. The flip requires migration, freeze and drain first.
Every resolution reads fresh routing; writes fence even an absent wildcard.
No table lock may span HTTP or generation. Callers commit prepared output first.
"""

import logging
import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from models.jobhunt_routing import JobhuntRouting, CONSUMER_SWISSJOB, PROFILE_WILDCARD
from services.matching.identity import resolve_core_profile_id
from .freeze import assert_document_writes_enabled


from .core_client import CoreDocuments
from .local import LocalDocuments
from .port import CoreUnavailableError, DocumentsPort, DocumentsUnsupportedError

logger = logging.getLogger(__name__)


class FallbackDocuments:
    """Canary de LECTURAS; create/delete siempre usan el escritor local.

    El resolver mantiene CoreDocuments sin vincular hasta el corte E.
    Unsupported esperado va a DEBUG; una lectura core averiada a WARNING.
    Nunca se reintenta una escritura ambigua contra otro almacen.
    """

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
        # E.2: canary is READ-only. A core timeout may hide a successful commit;
        # never try a second writer. Core writes need the separate E cutover.
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

    async def page(self, user_id, cursor=None):
        # Cross-authority cursors must never be replayed against another store.
        return await self._fallback.page(user_id, cursor=cursor)

    async def get(self, user_id, document_id):
        try:
            return await self._primary.get(user_id, document_id)
        except (CoreUnavailableError, DocumentsUnsupportedError) as exc:
            self._warn("get", exc)
            return await self._fallback.get(user_id, document_id)

    async def delete(self, user_id, document_id):
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
    db: AsyncSession,
    user_id: uuid.UUID | None = None,
    *,
    write=False,
) -> DocumentsPort:
    """Resolve the sole authority; never guess from cache on a routing error."""
    if write:
        assert_document_writes_enabled()
    pid = user_id or PROFILE_WILDCARD
    try:
        if write:
            await db.execute(text("LOCK TABLE jobhunt_routing IN SHARE MODE"))
        rows = (
            await db.execute(
                select(JobhuntRouting.profile_id, JobhuntRouting.mode).where(
                    JobhuntRouting.consumer_id == CONSUMER_SWISSJOB,
                    JobhuntRouting.capability == "documents",
                    JobhuntRouting.profile_id.in_([pid, PROFILE_WILDCARD]),
                )
            )
        ).all()
        modes = dict(rows)
        mode = modes.get(pid, modes.get(PROFILE_WILDCARD, "local"))
        if mode in {"local", "shadow", "core_read"}:
            return LocalDocuments(db)
        if mode not in {"core_primary", "rollback_pending"} or user_id is None:
            raise CoreUnavailableError("document routing or owner unavailable")
        profile_id = await resolve_core_profile_id(db, user_id)
        if profile_id is None:
            raise CoreUnavailableError("document owner is not bound")
        return CoreDocuments(profile_id=profile_id, user_id=user_id)
    except SQLAlchemyError:
        raise CoreUnavailableError("document routing unavailable") from None
