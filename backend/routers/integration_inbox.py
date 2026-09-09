"""At-least-once core receipts, committed before ACK; no domain data projection.

Document reads use their authority directly, not an inbox-derived copy. Keep only
metadata and a canonical hash to detect conflicting replays. Owner erase takes
the same user root lock and removes these receipts. No payload/CV is retained.
"""

import asyncio
import hashlib
import hmac
import json
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.requests import ClientDisconnect
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.integration_inbox import IntegrationInbox
from models.jobhunt_profile_map import JobhuntProfileMap
from models.user import User

router = APIRouter(prefix="/api/v1/integration/events", tags=["integration"])
MAX_BODY_BYTES = 65536


class DocumentChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: uuid.UUID
    profile_id: uuid.UUID
    version: int = Field(strict=True, ge=1, le=2)
    deleted: bool = Field(strict=True)


class CoreEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: uuid.UUID
    type: str = Field(min_length=1, max_length=100)
    aggregate: str = Field(min_length=1, max_length=100)
    aggregate_id: str = Field(min_length=1, max_length=100)
    subject_profile_id: uuid.UUID | None
    version: int = Field(strict=True, ge=1)
    payload: dict

    @model_validator(mode="after")
    def document_contract(self):
        if self.type == "document.changed":
            doc = DocumentChange.model_validate(self.payload)
            if (
                self.aggregate != "document"
                or doc.document_id != uuid.UUID(self.aggregate_id)
                or doc.profile_id != self.subject_profile_id
                or doc.version != self.version
                or doc.deleted != (doc.version == 2)
            ):
                raise ValueError("inconsistent document event")
        return self


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consumer_id: str = Field(min_length=1, max_length=100)
    event: CoreEvent


def authenticate(authorization: str | None = Header(default=None)):
    expected = settings.CORE_INBOX_TOKEN
    if not expected:
        raise HTTPException(503, "Integration inbox is not configured")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        token.encode(), expected.encode()
    ):
        raise HTTPException(401, "Invalid integration credential")


@router.post("", status_code=202, dependencies=[Depends(authenticate)])
async def receive_event(request: Request, db: AsyncSession = Depends(get_db)):
    # Manual bounded parsing means authentication runs before reading the body.
    body = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    raise HTTPException(413, "Integration event exceeds size limit")
                body.extend(chunk)
    except ClientDisconnect:
        raise HTTPException(400, "Client disconnected") from None
    except TimeoutError:
        raise HTTPException(408, "Integration event read timed out") from None
    try:
        envelope = Envelope.model_validate_json(bytes(body))
        if "\x00" in envelope.event.type:
            raise ValueError("invalid event type")
        canonical = json.dumps(
            envelope.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (ValueError, TypeError, RecursionError):
        # Never echo invalid inputs, since they can contain personal data.
        raise HTTPException(422, "Invalid integration event") from None
    if envelope.consumer_id != "swissjob-shadow":
        raise HTTPException(409, "Event addressed to another consumer")
    event = envelope.event
    if event.subject_profile_id is not None:
        uid = await db.scalar(
            select(JobhuntProfileMap.user_id).where(
                JobhuntProfileMap.core_profile_id == event.subject_profile_id
            )
        )
        if (
            uid is None
            or await db.scalar(
                select(User.id).where(User.id == uid).with_for_update(read=True)
            )
            is None
        ):
            raise HTTPException(409, "Event owner is not linked")
        # Root -> binding -> receipt is also the owner-delete lock order.
        bound = await db.scalar(
            select(JobhuntProfileMap.core_profile_id)
            .where(JobhuntProfileMap.user_id == uid)
            .with_for_update(read=True)
        )
        if bound != event.subject_profile_id:
            raise HTTPException(409, "Event owner binding changed")
    digest = hashlib.sha256(canonical).hexdigest()
    inserted = await db.scalar(
        insert(IntegrationInbox)
        .values(
            consumer_id=envelope.consumer_id,
            event_id=event.event_id,
            subject_profile_id=event.subject_profile_id,
            event_type=event.type,
            event_hash=digest,
        )
        .on_conflict_do_nothing()
        .returning(IntegrationInbox.event_id)
    )
    if inserted is None:
        previous = await db.scalar(
            select(IntegrationInbox.event_hash).where(
                IntegrationInbox.consumer_id == envelope.consumer_id,
                IntegrationInbox.event_id == event.event_id,
            )
        )
        if previous != digest:
            raise HTTPException(409, "Conflicting integration event replay")
    await db.commit()
    return {"accepted": True, "inserted": inserted is not None}
