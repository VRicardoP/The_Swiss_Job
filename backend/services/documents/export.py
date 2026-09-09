"""Account document export, not a cross-service transactional backup.

Reads the active authority and also preserves retained local output and prepared
deliveries. No partial success on HTTP failure, pagination loops or size limits.
HTTP never holds a local transaction. Historical cutover copies still require the
freeze/drain protocol; this user-facing export is not its replacement.
"""

import asyncio
import json

from sqlalchemy import select

from models.document_delivery import DocumentDelivery
from schemas.documents import DocumentDeliveryExport
from .core_client import CoreDocuments
from .local import LocalDocuments
from .port import CoreUnavailableError
from .seam import resolve_documents

MAX_EXPORT_ITEMS = 2000
MAX_EXPORT_BYTES = 32 * 1024 * 1024


async def export_documents(db, user_id):
    authority = await resolve_documents(db, user_id)
    result = {
        "documents": [],
        "retained_local_documents": [],
        "document_deliveries": [],
        "documents_authority": "core"
        if isinstance(authority, CoreDocuments)
        else "local",
    }
    size = 0

    def append(target, value):
        nonlocal size
        size += len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
        if size > MAX_EXPORT_BYTES or len(target) >= MAX_EXPORT_ITEMS:
            raise CoreUnavailableError(
                "Document export exceeds synchronous export limit"
            )
        target.append(value)

    async def collect(port, target):
        cursor, seen_cursors, seen_ids = None, set(), set()
        for _ in range(MAX_EXPORT_ITEMS // 20 + 1):
            page = await port.page(user_id, cursor=cursor)
            for doc in page.data:
                if doc.id in seen_ids:
                    raise CoreUnavailableError(
                        "Document export changed during pagination; retry"
                    )
                seen_ids.add(doc.id)
                append(target, doc.model_dump())
            if page.next_cursor is None:
                return
            if not page.data or page.next_cursor in seen_cursors:
                raise CoreUnavailableError("Document export cursor has no progress")
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
        raise CoreUnavailableError("Document export exceeds pagination limit")

    target = (
        result["retained_local_documents"]
        if isinstance(authority, CoreDocuments)
        else result["documents"]
    )
    await collect(LocalDocuments(db), target)
    rows = await db.scalars(
        select(DocumentDelivery)
        .where(DocumentDelivery.user_id == user_id)
        .order_by(DocumentDelivery.created_at, DocumentDelivery.operation_id)
        .limit(MAX_EXPORT_ITEMS + 1)
    )
    for row in rows:
        append(
            result["document_deliveries"],
            DocumentDeliveryExport.model_validate(row).model_dump(),
        )
    await db.commit()
    if isinstance(authority, CoreDocuments):
        try:
            async with asyncio.timeout(60):
                await collect(authority, result["documents"])
        except TimeoutError:
            raise CoreUnavailableError(
                "Document export timed out; no partial export returned"
            ) from None
    return result
