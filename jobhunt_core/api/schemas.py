"""Esquemas Pydantic de la API /v1 (A-09) — la FORMA la fija CONTRATOS §2;
aquí queda el esquema FORMAL (tipos/nullabilidad) que expone OpenAPI."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class ErrorDTO(BaseModel):
    code: str
    message: str
    details: dict = {}


class ListingDTO(BaseModel):
    source: str
    url: str
    apply_url: str | None = None


class PrimaryListingDTO(ListingDTO):
    external_id: str
    first_seen_at: datetime
    last_seen_at: datetime


class VacancyDTO(BaseModel):
    """DTO vacante multi-listing (§2): contenido de la offer_revision VIGENTE;
    solo vacantes ACTIVAS. `translations` existe en el esquema (A-06) pero no
    tiene escritor en la vertical de Fase A → lista vacía."""

    id: uuid.UUID
    title: str
    company: str | None = None
    description: str | None = None
    salary: str | None = None
    tags: list[str] = []
    location: str | None = None
    remote: bool | None = None
    primary_listing: PrimaryListingDTO | None = None
    listings: list[ListingDTO] = []
    translations: list = []


class ModelRefDTO(BaseModel):
    name: str
    version: str


class PolicyRefDTO(BaseModel):
    name: str
    prompt_version: str


class EvaluationDTO(BaseModel):
    """`matching_skills`/`missing_skills` son del rerank de Fase B: nulos en
    Fase A (score = coseno puro, `scores.similarity`)."""

    eval_key: str
    model: ModelRefDTO
    policy: PolicyRefDTO
    score_final: float
    scores: dict
    explanation: str | None = None
    matching_skills: list[str] | None = None
    missing_skills: list[str] | None = None


class MatchStateDTO(BaseModel):
    saved: bool
    dismissed: bool
    feedback: str | None = None
    notes: str | None = None


class MatchDTO(BaseModel):
    vacancy: VacancyDTO
    evaluation: EvaluationDTO
    state: MatchStateDTO


class MatchesPageDTO(BaseModel):
    items: list[MatchDTO]
    next_cursor: str | None = None


class ProfileRevisionDTO(BaseModel):
    content: dict
    content_hash: str
    text_hash: str


class ProfileDTO(BaseModel):
    id: uuid.UUID
    external_ref: str
    created_at: datetime
    current_revision: ProfileRevisionDTO | None = None
