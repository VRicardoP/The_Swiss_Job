"""Pydantic schemas for AI document generation endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocType(str, Enum):
    cv = "cv"
    cover_letter = "cover_letter"


class GenerateDocumentRequest(BaseModel):
    """Request body for POST /api/v1/documents/generate."""

    operation_id: uuid.UUID | None = None
    job_hash: str = Field(..., max_length=36)
    doc_type: DocType
    language: str = Field("en", max_length=5, pattern=r"^(en|de|fr|it)$")

    @field_validator("job_hash")
    @classmethod
    def canonical_job_reference(cls, value):
        # Existing local references retain their contract. Longer references
        # must be canonical core UUIDs, not alternate spellings or aliases.
        if len(value) > 32 and str(uuid.UUID(value)) != value:
            raise ValueError("Expected a canonical core vacancy UUID")
        return value


class GeneratedDocumentResponse(BaseModel):
    """Response with generated document content."""

    id: uuid.UUID
    job_hash: str
    doc_type: DocType
    content: str
    language: str | None = None
    created_at: datetime
    job_title: str | None = None
    job_company: str | None = None


class DocumentListResponse(BaseModel):
    """List of generated documents for a user+job pair."""

    data: list[GeneratedDocumentResponse]
    total: int


class DocumentOperationResponse(BaseModel):
    operation_id: uuid.UUID
    status: Literal["pending", "delivered"]
    document_id: uuid.UUID | None = None
    error: str | None = None


class DocumentPageResponse(BaseModel):
    data: list[GeneratedDocumentResponse]
    next_cursor: str | None = None


class DocumentDeliveryExport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    operation_id: uuid.UUID
    profile_id: uuid.UUID
    payload: dict | None
    payload_hash: str
    request_hash: str
    document_id: uuid.UUID | None
    created_at: datetime
    first_attempt_at: datetime | None
    delivered_at: datetime | None
    last_error: str | None
