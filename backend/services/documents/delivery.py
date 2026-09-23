"""Recoverable single-document delivery, not a generation queue.

The caller commits prepared output before HTTP. Retries use precisely the first
committed body and operation key, never the LLM. Network calls hold no transaction.
The 23h retry bound is smaller than the core's 24h receipt TTL; expired/unknown
outcomes require reconciliation, not a new key. Completed rows contain no CV text.
Cutover/erase must freeze and drain requests before changing bindings/authority.
"""

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from config import settings
from models.document_delivery import DocumentDelivery
from models.jobhunt_profile_map import JobhuntProfileMap
from models.user import User
from .core_client import CoreDocuments
from .seam import resolve_documents

RETRY_WINDOW = timedelta(hours=23)


class DocumentDeliveryError(Exception):
    """Safe code only, without HTTP body, credentials or generated content."""


def request_hash(job_hash, doc_type, language):
    return hashlib.sha256(
        json.dumps([job_hash, doc_type, language], separators=(",", ":")).encode()
    ).hexdigest()


async def _lock_owner(db, user_id):
    if (
        await db.scalar(
            select(User.id)
            .where(User.id == user_id, User.is_active.is_(True))
            .with_for_update(read=True)
        )
        is None
    ):
        raise DocumentDeliveryError("owner_unavailable")


async def _binding(db, user_id, profile_id):
    # Same root lock as owner deletion, before mapping/journal/FK operations.
    await _lock_owner(db, user_id)
    bound = await db.scalar(
        select(JobhuntProfileMap.core_profile_id)
        .where(JobhuntProfileMap.user_id == user_id)
        .with_for_update(read=True)
    )
    if bound != profile_id:
        raise DocumentDeliveryError("profile_binding_changed")


async def enqueue(
    db,
    *,
    operation_id,
    user_id,
    profile_id,
    job_hash,
    doc_type,
    language,
    content,
    job_title=None,
    job_company=None,
):
    """First committed output wins; caller owns commit and routing/freeze checks."""
    if not all(
        isinstance(value, uuid.UUID) for value in (operation_id, user_id, profile_id)
    ):
        raise DocumentDeliveryError("invalid_identity")
    if not isinstance(content, str) or not content or "\x00" in content:
        raise DocumentDeliveryError("invalid_content")
    try:
        encoded = content.encode("utf-8")
    except UnicodeError:
        raise DocumentDeliveryError("invalid_content") from None
    if len(encoded) > 1_000_000:
        raise DocumentDeliveryError("invalid_content")
    await _binding(db, user_id, profile_id)
    payload = dict(
        job_hash=job_hash,
        doc_type=doc_type,
        language=language,
        content=content,
        job_title=job_title,
        job_company=job_company,
    )
    digest = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()
    identity = request_hash(job_hash, doc_type, language)
    await db.execute(
        insert(DocumentDelivery)
        .values(
            operation_id=operation_id,
            user_id=user_id,
            profile_id=profile_id,
            request_hash=identity,
            payload_hash=digest,
            payload=payload,
        )
        .on_conflict_do_nothing(index_elements=[DocumentDelivery.operation_id])
    )
    row = await db.scalar(
        select(DocumentDelivery)
        .where(DocumentDelivery.operation_id == operation_id)
        .execution_options(populate_existing=True)
    )
    if (row.user_id, row.profile_id, row.request_hash) != (
        user_id,
        profile_id,
        identity,
    ):
        raise DocumentDeliveryError("operation_conflict")
    # Two simultaneous generations may differ; both deliver the FIRST persisted
    # output. This operation represents one user action, not a content cache.
    return operation_id


async def operation_status(db, operation_id, user_id, *, expected_request_hash=None):
    row = await db.scalar(
        select(DocumentDelivery).where(
            DocumentDelivery.operation_id == operation_id,
            DocumentDelivery.user_id == user_id,
        )
    )
    if row is None:
        return None
    await _binding(db, user_id, row.profile_id)
    if expected_request_hash is not None and row.request_hash != expected_request_hash:
        raise DocumentDeliveryError("operation_conflict")
    return {
        "operation_id": operation_id,
        "status": "delivered" if row.delivered_at else "pending",
        "document_id": row.document_id,
        "error": row.last_error,
    }


async def deliver(session_factory, operation_id, user_id, *, sender_factory=None):
    """Deliver a committed journal row, returning a safe, recoverable status."""
    try:
        async with session_factory() as db:
            if settings.DOCUMENT_WRITES_FROZEN:
                raise DocumentDeliveryError("writes_frozen")
            authority = await resolve_documents(db, user_id, write=True)
            if not isinstance(authority, CoreDocuments):
                raise DocumentDeliveryError("authority_changed")
            await _lock_owner(db, user_id)
            row = await db.scalar(
                select(DocumentDelivery)
                .where(
                    DocumentDelivery.operation_id == operation_id,
                    DocumentDelivery.user_id == user_id,
                )
                .with_for_update()
            )
            if row is None:
                raise DocumentDeliveryError("operation_not_found")
            await _binding(db, user_id, row.profile_id)
            if authority.profile_id != row.profile_id:
                raise DocumentDeliveryError("profile_binding_changed")
            if row.delivered_at:
                return {
                    "operation_id": operation_id,
                    "status": "delivered",
                    "document_id": row.document_id,
                    "error": None,
                }
            now = await db.scalar(select(func.clock_timestamp()))
            if row.first_attempt_at and now >= row.first_attempt_at + RETRY_WINDOW:
                raise DocumentDeliveryError("receipt_window_expired")
            if row.first_attempt_at is None:
                row.first_attempt_at = now
            pid, payload = row.profile_id, deepcopy(row.payload)
            digest = row.payload_hash
            await db.commit()
        sender = (
            sender_factory
            or (lambda pid, uid: CoreDocuments(profile_id=pid, user_id=uid))
        )(pid, user_id)
        response = await sender.create(user_id, operation_id=operation_id, **payload)
        async with session_factory() as db:
            # No resurrection after owner erase, nor a receipt for a different
            # binding. A late ACK may not recreate a journal that was removed.
            await _binding(db, user_id, pid)
            acknowledged = await db.scalar(
                update(DocumentDelivery)
                .where(
                    DocumentDelivery.operation_id == operation_id,
                    DocumentDelivery.user_id == user_id,
                    DocumentDelivery.payload_hash == digest,
                    DocumentDelivery.delivered_at.is_(None),
                )
                .values(
                    document_id=response.id,
                    delivered_at=func.clock_timestamp(),
                    payload=None,
                    last_error=None,
                )
                .returning(DocumentDelivery.operation_id)
            )
            if acknowledged is None:
                existing = await operation_status(db, operation_id, user_id)
                if not existing or existing["document_id"] != response.id:
                    raise DocumentDeliveryError("receipt_conflict")
            await db.commit()
        return {
            "operation_id": operation_id,
            "status": "delivered",
            "document_id": response.id,
            "error": None,
        }
    except Exception as exc:
        error = (
            str(exc) if isinstance(exc, DocumentDeliveryError) else "delivery_failed"
        )
        # Diagnostics failure cannot turn a committed prepared operation into
        # an untracked error; the next explicit retry still uses its original ID.
        try:
            async with session_factory() as db:
                await db.execute(
                    update(DocumentDelivery)
                    .where(
                        DocumentDelivery.operation_id == operation_id,
                        DocumentDelivery.user_id == user_id,
                        DocumentDelivery.delivered_at.is_(None),
                    )
                    .values(last_error=error)
                )
                await db.commit()
                completed = await operation_status(db, operation_id, user_id)
                if completed and completed["status"] == "delivered":
                    return completed
        except Exception:
            pass
        return {
            "operation_id": operation_id,
            "status": "pending",
            "document_id": None,
            "error": error,
        }
