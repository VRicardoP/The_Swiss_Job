"""Esquemas Pydantic de la API /v1 (A-09) — la FORMA la fija CONTRATOS §2;
aquí queda el esquema FORMAL (tipos/nullabilidad) que expone OpenAPI."""

import uuid
from datetime import date, datetime
from typing import Any, Literal

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


# ---------------------------------------------------------------- C-4 (v2.1)

# Enum del core (8 estados, core0011); el del portfolio (6) es subconjunto.
ApplicationStatus = Literal[
    "saved", "applied", "phone_screen", "technical", "interview",
    "offer", "rejected", "withdrawn",
]
NotifyFrequency = Literal["realtime", "daily", "weekly"]


class ApplicationDTO(BaseModel):
    """Item del GET compuesto (Decisión 5): kind=application|bookmark. Los
    campos presentables llevan precedencia snapshot-primero (una clave
    presente en snapshot prima aunque valga null). Bookmark puro:
    id=vacancy_id, kind=bookmark, status=saved, notes de
    profile_vacancy_state, corpus de la vacante."""

    id: uuid.UUID
    profile_id: uuid.UUID
    vacancy_id: uuid.UUID
    kind: Literal["application", "bookmark"]
    status: ApplicationStatus
    notes: str | None = None
    follow_up_date: date | None = None
    created_at: datetime
    updated_at: datetime
    title: str | None = None
    company: str | None = None
    url: str | None = None
    source: str | None = None
    description: str | None = None


class ApplicationsPageDTO(BaseModel):
    items: list[ApplicationDTO]
    next_cursor: str | None = None


class ApplicationCreateDTO(BaseModel):
    """POST /v1/applications (Decisión 3): vacancy_id directo O url (nullable
    — entrada manual, R2-4). `status` ausente → saved (paridad con el puerto
    real del BFF, Decisión 4)."""

    profile_id: uuid.UUID
    vacancy_id: uuid.UUID | None = None
    url: str | None = Field(None, max_length=2048)
    title: str = Field(..., min_length=1, max_length=500)
    company: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=100_000)
    source: str | None = Field(None, max_length=200)
    status: ApplicationStatus | None = None
    notes: str | None = Field(None, max_length=20_000)
    follow_up_date: date | None = None


class ApplicationPatchDTO(BaseModel):
    """PATCH parcial: solo los campos PRESENTES mutan (model_fields_set)."""

    status: ApplicationStatus | None = None
    notes: str | None = Field(None, max_length=20_000)
    follow_up_date: date | None = None


class BookmarkItemDTO(BaseModel):
    """Item de PUT /v1/profiles/{pid}/bookmarks: mismo vínculo que el POST
    (Decisión 3, incl. camino sin url) sin `status` (siempre saved)."""

    vacancy_id: uuid.UUID | None = None
    url: str | None = Field(None, max_length=2048)
    title: str = Field(..., min_length=1, max_length=500)
    company: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=100_000)
    source: str | None = Field(None, max_length=200)
    notes: str | None = Field(None, max_length=20_000)
    follow_up_date: date | None = None


class BookmarksPutDTO(BaseModel):
    bookmarks: list[BookmarkItemDTO] = Field(..., max_length=500)


class BookmarksSyncResultDTO(BaseModel):
    """Respuesta del sync ADITIVO: SOLO las applications creadas en este PUT
    (paridad con sync_bookmarks real: crea, no borra ausentes)."""

    created: list[ApplicationDTO]


class SavedSearchDTO(BaseModel):
    """Decisión 5: client-writable (name..is_active) + engine-owned de solo
    lectura (id, last_run_at, total_matches, created_at, updated_at)."""

    id: uuid.UUID
    profile_id: uuid.UUID
    name: str
    filters: dict
    min_score: int
    notify_frequency: NotifyFrequency
    notify_push: bool
    is_active: bool
    last_run_at: datetime | None = None
    total_matches: int
    created_at: datetime
    updated_at: datetime


class SavedSearchesPageDTO(BaseModel):
    items: list[SavedSearchDTO]
    next_cursor: str | None = None


class SavedSearchCreateDTO(BaseModel):
    """POST /v1/saved-searches. `filters` se valida a OBJETO en el endpoint
    (400 invalid_filters — R2-6); ausentes → defaults del core (daily/true)."""

    profile_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    filters: Any = None
    min_score: int | None = Field(None, ge=0, le=100)
    notify_frequency: NotifyFrequency | None = None
    notify_push: bool | None = None
    is_active: bool | None = None


class SavedSearchPutDTO(BaseModel):
    """PUT completo SOLO de client-writable (Decisión 5): los AUSENTES
    conservan el valor vigente; engine-owned se IGNORAN si llegan (extra
    keys las descarta Pydantic)."""

    name: str | None = Field(None, min_length=1, max_length=200)
    filters: Any = None
    min_score: int | None = Field(None, ge=0, le=100)
    notify_frequency: NotifyFrequency | None = None
    notify_push: bool | None = None
    is_active: bool | None = None
