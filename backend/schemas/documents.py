"""Pydantic schemas for AI document generation endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class DocType(str, Enum):
    cv = "cv"
    cover_letter = "cover_letter"


class GenerateDocumentRequest(BaseModel):
    """Request body for POST /api/v1/documents/generate."""

    job_hash: str = Field(..., max_length=32)
    doc_type: DocType
    language: str = Field("en", max_length=5, pattern=r"^(en|de|fr|it)$")


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
