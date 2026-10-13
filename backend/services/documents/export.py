"""Account document export, not a cross-service transactional backup.

Reads the active authority and also preserves retained local output and prepared
deliveries. No partial success on HTTP failure, pagination loops or size limits.
HTTP never holds a local transaction. Historical cutover copies still require the
freeze/drain protocol; this user-facing export is not its replacement.
"""

import asyncio
import base64
import json
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select, tuple_

from models.document_delivery import DocumentDelivery
from models.generated_document import GeneratedDocument
from schemas.documents import (
    DocumentDeliveryExport,
    DocumentPageResponse,
    GeneratedDocumentResponse,
)
from .port import CoreUnavailableError
from .seam import resolve_documents


class _LocalDocumentArchive:
    """Read-only access to retained pre-cutover documents for GDPR export."""

    def __init__(self, db):
        self._db = db

    async def page(self, user_id, cursor=None):
        stmt = select(GeneratedDocument).where(GeneratedDocument.user_id == user_id)
        if cursor:
            try:
                if len(cursor) > 512:
                    raise ValueError
                stamp, identity = json.loads(
                    base64.b64decode(cursor, altchars=b"-_", validate=True)
                )
                stamp, identity = datetime.fromisoformat(stamp), uuid.UUID(identity)
                if stamp.tzinfo is None:
                    raise ValueError
            except (ValueError, TypeError, UnicodeError):
                raise HTTPException(400, "Invalid document cursor") from None
            stmt = stmt.where(
                tuple_(GeneratedDocument.created_at, GeneratedDocument.id)
                < tuple_(stamp, identity)
            )
        docs = (
            await self._db.scalars(
                stmt.order_by(
                    GeneratedDocument.created_at.desc(), GeneratedDocument.id.desc()
                ).limit(21)
            )
        ).all()
        next_cursor = None
        if len(docs) > 20:
            last = docs[19]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps([last.created_at.isoformat(), str(last.id)]).encode()
            ).decode()
        return DocumentPageResponse(
            data=[
                GeneratedDocumentResponse(
                    id=doc.id,
                    job_hash=doc.job_hash,
                    doc_type=doc.doc_type,
                    content=doc.content,
                    language=doc.language,
                    created_at=doc.created_at,
                    job_title=doc.job_title,
                    job_company=doc.job_company,
                )
                for doc in docs[:20]
            ],
            next_cursor=next_cursor,
        )


MAX_EXPORT_ITEMS = 2000
MAX_EXPORT_BYTES = 32 * 1024 * 1024


async def export_documents(db, user_id):
    authority = await resolve_documents(db, user_id)
    result = {
        "documents": [],
        "retained_local_documents": [],
        "document_deliveries": [],
        "documents_authority": "core",
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

    await collect(_LocalDocumentArchive(db), result["retained_local_documents"])
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
    try:
        async with asyncio.timeout(60):
            await collect(authority, result["documents"])
    except TimeoutError:
        raise CoreUnavailableError(
            "Document export timed out; no partial export returned"
        ) from None
    return result
