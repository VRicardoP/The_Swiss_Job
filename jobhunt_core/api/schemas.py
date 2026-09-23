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
    # Idioma de la canónica. ADITIVO y OPCIONAL: existía en el contenido
    # canónico desde el proyector, pero no lo declaraba nadie aguas abajo, así
    # que el consumidor lo detectaba por título en CADA oferta servida —
    # 50,1 ms x 1.800 ofertas por petición (punto 5, 2026-09-22). Ausente
    # significa «no lo sé»: el consumidor vuelve a detectarlo, que es el
    # comportamiento de siempre.
    language: str | None = None
    primary_listing: PrimaryListingDTO | None = None
    listings: list[ListingDTO] = []
    translations: list = []


class MatchesVersionDTO(BaseModel):
    """Versión del feed de un perfil (§2): digest + tamaño.

    Deja que el consumidor sepa si el feed cambió SIN descargarlo. Antes, la
    única forma de saberlo era recorrer las 18 páginas: el 87-89 % del coste
    de servir la pantalla principal. Un `If-None-Match` no lo evitaba, porque
    el ETag se deriva del payload y construirlo es el trabajo.

    `version` es opaca: su composición puede cambiar sin avisar y el
    consumidor sólo debe compararla con la que guardó. Lo único garantizado es
    que **si el feed servido cambia, la versión cambia** — incluido el estado
    de usuario (`feedback`, `saved`) y el listing primario; quedan fuera los
    listings no primarios y `notes` (ver `matching.feed_version_sql`). No se
    garantiza lo contrario: dos versiones distintas pueden describir feeds
    iguales.
    """

    version: str
    total: int = Field(ge=0)


class VacanciesPageDTO(BaseModel):
    """Página del feed de catálogo (C-API-R): VacancyDTO reutilizado + cursor
    keyset OPACO. Con offset explícito incluye el total para el BFF existente."""

    items: list[VacancyDTO]
    next_cursor: str | None = None
    total: int | None = Field(default=None, ge=0)


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
    # Tamaño del feed completo, con el MISMO contrato que la página. Aditivo:
    # un consumidor antiguo lo ignora. Sin él, el BFF recorría el feed entero
    # sólo para contarlo — 18 peticiones y ~9 s para servir 20 ofertas.
    total: int | None = None


class ProfileRevisionDTO(BaseModel):
    content: dict
    content_hash: str
    text_hash: str


class ProfileDTO(BaseModel):
    id: uuid.UUID
    external_ref: str
    created_at: datetime
    current_revision: ProfileRevisionDTO | None = None


class ExclusionDTO(BaseModel):
    """Una regla de exclusión de candidatos del perfil."""

    kind: str
    pattern: str


class ExclusionsWriteDTO(BaseModel):
    """Cuerpo del PUT /v1/profiles/{pid}/exclusions — conjunto COMPLETO.

    Declarativo a propósito (revisión externa 2026-09-07): altas y bajas
    viajan por el mismo camino, así que una baja no puede perderse. El core
    es el único escritor efectivo de esta configuración porque es quien
    sirve el feed que ella determina.
    """

    exclusions: list[ExclusionDTO]
    version: int = Field(ge=1, le=9223372036854775807)


class ExclusionsDTO(BaseModel):
    """Representación de las exclusiones vigentes."""

    exclusions: list[ExclusionDTO]
    version: int


class ProfileWriteDTO(BaseModel):
    """Cuerpo del PUT /v1/profiles/{pid} (C-3 CV push + preferencias, PF.5).

    Fase 2 del cierre v5: además del subconjunto EMBEBIBLE (title + cv_text +
    skills), acepta las preferencias de CONTENT_FIELDS como campos OPCIONALES.
    Semántica de omisión (mismo invariante que la defensa TOAST del CDC): un
    campo NO enviado se PRESERVA desde la revisión vigente — lo que no envías
    no lo borras. El C-3 actual, que solo manda title/cv_text/skills, sigue
    siendo válido tal cual. save_profile_revision es idempotente por
    content_hash (re-PUT del mismo contenido no crea revisión)."""

    # Cotas de tamaño en la FRONTERA (1ª rev.): el CV push viene del BFF; un
    # cv_text sin tope inflaría la revisión y su embedding. Holgados pero
    # finitos.
    title: str | None = Field(None, max_length=500)
    cv_text: str | None = Field(None, max_length=100_000)
    skills: list[str] = Field(default=[], max_length=200)
    languages: list[str] = Field(default=[], max_length=50)
    locations: list[str] = Field(default=[], max_length=50)
    experience_years: int | None = Field(None, ge=0, le=80)
    salary_min: int | None = Field(None, ge=0)
    salary_max: int | None = Field(None, ge=0)
    # P2 revisión 2026-09-03: la frontera autoritativa (legacy
    # RemotePreference) solo admite estos valores — "banana" creaba una
    # revisión válida que el rerank interpretaba como neutral en silencio.
    remote_pref: Literal["remote_only", "hybrid", "onsite", "any"] | None = None
    # Intención laboral explícita (Fase 2): roles objetivo del usuario, cada
    # uno acotado; el push C-3 que no lo envía lo PRESERVA (omisión conserva).
    target_roles: list[str] = Field(default=[], max_length=10)


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


class BookmarkSkippedDTO(BaseModel):
    """Item del PUT de bookmarks SALTEADO por irresoluble (G1-P3-2): vacante
    archivada tras el snapshot del BFF (Decisión 3a). Identidad mínima para
    que el BFF lo reconcilie; jamás aborta el lote entero."""

    vacancy_id: uuid.UUID | None = None
    url: str | None = None
    title: str
    reason: str


class BookmarksSyncResultDTO(BaseModel):
    """Respuesta del sync ADITIVO: SOLO las applications creadas en este PUT
    (paridad con sync_bookmarks real: crea, no borra ausentes) + los items
    salteados por irresolubles (G1-P3-2: un dato rancio del snapshot no
    convierte el sync en un 404 estructuralmente sin progreso)."""

    created: list[ApplicationDTO]
    skipped: list[BookmarkSkippedDTO] = Field(default_factory=list)


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
    # Explicit dialect/authority opt-in; existing clients remain CRUD-only.
    execution_contract: Literal["swissjob-v1"] | None = None


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
