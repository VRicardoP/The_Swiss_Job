"""Pydantic schemas for AI matching endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MatchAnalyzeRequest(BaseModel):
    """Request body for POST /api/v1/match/analyze."""

    top_k: int = Field(default=20, ge=1, le=100)


class MatchScoreBreakdown(BaseModel):
    """Individual score components."""

    embedding: float
    salary: float
    location: float
    recency: float
    llm: float = 0.0


class MatchResultResponse(BaseModel):
    """Single match result with job summary."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_hash: str
    score_final: float
    scores: MatchScoreBreakdown
    explanation: str | None = None
    matching_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    feedback: str | None = None
    created_at: datetime

    # Denormalized job fields for display
    job_title: str | None = None
    job_company: str | None = None
    job_location: str | None = None
    job_url: str | None = None
    job_salary_min: int | None = None
    job_salary_max: int | None = None
    job_tags: list[str] = Field(default_factory=list)
    job_source: str | None = None


class MatchAnalyzeResponse(BaseModel):
    """Response for POST /api/v1/match/analyze."""

    status: str
    total_candidates: int = 0
    results_count: int = 0


class MatchResultsResponse(BaseModel):
    """Paginated match results for GET /api/v1/match/results."""

    data: list[MatchResultResponse]
    total: int
    weights_used: dict[str, float]


class MatchFeedbackRequest(BaseModel):
    """Request body for POST /api/v1/match/{job_hash}/feedback."""

    feedback: str = Field(..., pattern=r"^(thumbs_up|thumbs_down|applied|dismissed)$")


class MatchFeedbackResponse(BaseModel):
    """Response for feedback submission."""

    status: str
    job_hash: str
    feedback: str


class ImplicitFeedbackRequest(BaseModel):
    """Request body for POST /api/v1/match/{job_hash}/implicit."""

    action: str = Field(
        ...,
        pattern=r"^(opened|view_time|saved|applied|dismissed|skipped)$",
    )
    duration_ms: int | None = Field(default=None, ge=0)


class ImplicitFeedbackResponse(BaseModel):
    """Response for implicit feedback submission."""

    status: str
    job_hash: str
    action: str
