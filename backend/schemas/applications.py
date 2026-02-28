"""Pydantic schemas for job application tracking endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from models.enums import ApplicationStatus


class ApplicationCreate(BaseModel):
    """Request body for POST /api/v1/applications."""

    job_hash: str = Field(..., max_length=32)
    notes: str | None = Field(None, max_length=2000)


class ApplicationUpdate(BaseModel):
    """Request body for PATCH /api/v1/applications/{id}."""

    status: ApplicationStatus | None = None
    notes: str | None = Field(None, max_length=2000)
    follow_up_date: datetime | None = None
    applied_url: str | None = Field(None, max_length=500)


class ApplicationResponse(BaseModel):
    """Single application with denormalized job info."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    job_hash: str
    status: ApplicationStatus
    notes: str | None = None
    applied_at: datetime | None = None
    applied_url: str | None = None
    follow_up_date: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # Denormalized job fields for display
    job_title: str | None = None
    job_company: str | None = None
    job_location: str | None = None
    job_source: str | None = None


class ApplicationsListResponse(BaseModel):
    """Paginated application list with status summary."""

    data: list[ApplicationResponse]
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)


class ApplicationStatsResponse(BaseModel):
    """Application pipeline statistics."""

    by_status: dict[str, int] = Field(default_factory=dict)
    conversion_rates: dict[str, float] = Field(default_factory=dict)
    by_source: dict[str, int] = Field(default_factory=dict)
