"""Schemas para el sistema de análisis de patrones y filtros de usuario."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class PatternSuggestionResponse(BaseModel):
    id: uuid.UUID
    suggestion_type: str
    pattern: str
    description: str | None
    confidence: float
    sample_jobs: list[dict]
    affected_count: int
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PatternSuggestionsResponse(BaseModel):
    data: list[PatternSuggestionResponse]
    total: int


class ReviewSuggestionRequest(BaseModel):
    action: str = Field(pattern=r"^(approve|reject)$")


class ReviewSuggestionResponse(BaseModel):
    status: str
    suggestion_id: uuid.UUID
    filter_id: uuid.UUID | None = None


class JobFilterResponse(BaseModel):
    id: uuid.UUID
    filter_type: str
    pattern: str
    description: str | None
    hit_count: int
    is_active: bool
    source: str
    created_at: datetime
    approved_at: datetime | None

    model_config = {"from_attributes": True}


class JobFiltersResponse(BaseModel):
    data: list[JobFilterResponse]
    total: int


class CreateFilterRequest(BaseModel):
    filter_type: str = Field(pattern=r"^(title_contains|tag_contains)$")
    pattern: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=500)


class AnalyzeRejectedRequest(BaseModel):
    min_rejected: int = Field(default=3, ge=1, le=100)


class AnalyzeRejectedResponse(BaseModel):
    status: str
    suggestions_generated: int
    rejected_jobs_analyzed: int
