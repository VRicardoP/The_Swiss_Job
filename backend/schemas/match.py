"""Pydantic schemas for AI matching endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MatchAnalyzeRequest(BaseModel):
    """Request body for POST /api/v1/match/analyze."""

    min_score: float = Field(default=35.0, ge=0, le=100)


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

    # Watchlist colegios suizos: state machine + urgency boost + carta
    application_status: str = "detected"
    urgency_score: float = 0.0
    has_draft: bool = False
    school_id: str | None = None     # Si pertenece a la watchlist, slug del colegio
    school_policy: str | None = None # direct_email_ok, portal_only, etc.

    created_at: datetime

    # Denormalized job fields for display
    job_title: str | None = None
    job_title_en: str | None = None
    job_language: str | None = None
    job_company: str | None = None
    job_location: str | None = None
    job_url: str | None = None
    job_description: str | None = None  # snippet corto para la tarjeta
    job_salary_min: int | None = None
    job_salary_max: int | None = None
    job_tags: list[str] = Field(default_factory=list)
    job_source: str | None = None
    job_category: str | None = None  # A–M o "otros"


class ApplicationStatusRequest(BaseModel):
    """Cambio de estado en el state machine de candidatura."""

    application_status: str = Field(
        ...,
        pattern=(
            r"^(detected|reviewed|drafted|sent|awaiting|followup_due|"
            r"interview|closed_positive|closed_negative)$"
        ),
    )


class ApplicationStatusResponse(BaseModel):
    status: str
    job_hash: str
    application_status: str


class GenerateDraftRequest(BaseModel):
    """Solicita generar borrador de carta a partir de plantilla del colegio."""

    template_override: str | None = Field(
        default=None, pattern=r"^[AB]$"
    )  # Si se pasa, ignora school.template_id


class GenerateDraftResponse(BaseModel):
    status: str
    job_hash: str
    draft_letter: str


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
    feedback: str | None


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
