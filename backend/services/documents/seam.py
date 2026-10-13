"""Fixed Core authority for generated documents."""

import uuid

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from services.matching.identity import resolve_core_profile_id

from .core_client import CoreDocuments
from .freeze import assert_document_writes_enabled
from .port import CoreUnavailableError, DocumentsPort


async def resolve_documents(
    db: AsyncSession,
    user_id: uuid.UUID | None = None,
    *,
    write=False,
) -> DocumentsPort:
    if write:
        assert_document_writes_enabled()
    if user_id is None:
        raise CoreUnavailableError("document owner unavailable")
    try:
        profile_id = await resolve_core_profile_id(db, user_id)
    except SQLAlchemyError:
        raise CoreUnavailableError("document owner lookup unavailable") from None
    if profile_id is None:
        raise CoreUnavailableError("document owner is not bound")
    return CoreDocuments(profile_id=profile_id, user_id=user_id)
