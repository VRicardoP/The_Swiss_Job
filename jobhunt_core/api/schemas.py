"""Esquemas Pydantic de la API /v1 (A-09) — la FORMA la fija CONTRATOS §2;
aquí queda el esquema FORMAL (tipos/nullabilidad) que expone OpenAPI."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ErrorDTO(BaseModel):
    code: str
    message: str
    details: dict = {}


class ListingDTO(BaseModel):
    """`external_id` en TODOS los listings activos (P2 rev. externa A.SEAM):
    los alias legacy NO-primary tambien portan su MD5 accionable — sin el,
    el BFF no puede resolver la identidad legacy de un attach por URL.
    OJO ETag: añadirlo cambio la REPRESENTACION; el ETag se deriva del
    payload (v1._etag_of), asi que un If-None-Match previo al cambio deja de
    revalidar y el cliente recibe 200 con la forma nueva (versionado
    correcto por construccion — test_integration_api lo fija)."""

    source: str
    external_id: str
    url: str
    apply_url: str | None = None


class PrimaryListingDTO(ListingDTO):
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


class VacanciesPageDTO(BaseModel):
    """Página del feed de catálogo (C-API-R): VacancyDTO reutilizado + cursor
    keyset OPACO. Misma forma de página que MatchesPageDTO."""

    items: list[VacancyDTO]
    next_cursor: str | None = None


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


class ProfileWriteDTO(BaseModel):
    """Cuerpo del PUT /v1/profiles/{pid} (C-3 CV push, contenido PF.5): el
    subconjunto EMBEBIBLE del perfil (title + cv_text + skills). El resto de
    CONTENT_FIELDS (idiomas/ubicaciones/salario…) NO viaja en el CV push —
    normalize_profile los deja en su default. save_profile_revision es
    idempotente por content_hash (re-PUT del mismo CV no crea revisión)."""

    # Cotas de tamaño en la FRONTERA (1ª rev.): el CV push viene del BFF; un
    # cv_text sin tope inflaría la revisión y su embedding. Holgados pero
    # finitos.
    title: str | None = Field(None, max_length=500)
    cv_text: str | None = Field(None, max_length=100_000)
    skills: list[str] = Field(default=[], max_length=200)
