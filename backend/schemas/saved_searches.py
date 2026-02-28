"""Pydantic schemas for saved search endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from models.enums import NotifyFrequency


class SavedSearchCreate(BaseModel):
    """Request body for POST /api/v1/searches."""

    name: str = Field(..., min_length=1, max_length=200)
    filters: dict = Field(default_factory=dict)
    min_score: int = Field(0, ge=0, le=100)
    notify_frequency: NotifyFrequency = NotifyFrequency.daily
    notify_push: bool = True


class SavedSearchUpdate(BaseModel):
    """Request body for PUT /api/v1/searches/{id}."""

    name: str | None = Field(None, min_length=1, max_length=200)
    filters: dict | None = None
    min_score: int | None = Field(None, ge=0, le=100)
    notify_frequency: NotifyFrequency | None = None
    notify_push: bool | None = None
    is_active: bool | None = None


class SavedSearchResponse(BaseModel):
    """Single saved search."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    filters: dict
    min_score: int
    notify_frequency: NotifyFrequency
    notify_push: bool
    is_active: bool
    last_run_at: datetime | None = None
    total_matches: int
    created_at: datetime


class SavedSearchListResponse(BaseModel):
    """Paginated saved searches list."""

    data: list[SavedSearchResponse]
    total: int
