import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from models.enums import RemotePreference


def _empty_list_if_null(v):
    """G3/P2-7: columna JSONB NOT NULL envenenada con `'null'::jsonb`.

    SQLAlchemy la devuelve como None y la lectura reventaba con 500 PARA
    SIEMPRE (GET /profile y GET /profile/export, portabilidad GDPR). Se lee
    como lista vacia — el valor que la columna deberia tener — para que una
    fila ya corrupta siga siendo legible y reparable desde la UI.
    """
    return [] if v is None else v


class ProfileData(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str | None
    skills: list
    experience_years: int | None
    languages: list
    locations: list
    salary_min: int | None
    salary_max: int | None
    remote_pref: str
    cv_text: str | None
    score_weights: dict | None
    watchlist_schools_enabled: bool = False
    updated_at: datetime

    _null_json_as_empty = field_validator(
        "skills", "languages", "locations", mode="before"
    )(_empty_list_if_null)


class ProfileUpdate(BaseModel):
    """Schema for PUT /api/v1/profile — update user preferences."""

    title: str | None = Field(None, max_length=200)
    skills: list[str] | None = None
    experience_years: int | None = Field(None, ge=0, le=50)
    languages: list[str] | None = None
    locations: list[str] | None = None
    salary_min: int | None = Field(None, ge=0)
    salary_max: int | None = Field(None, ge=0)
    remote_pref: RemotePreference | None = None
    score_weights: dict[str, float] | None = None
    watchlist_schools_enabled: bool | None = None

    @model_validator(mode="after")
    def validate_salary_range(self):
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min must be <= salary_max")
        return self

    @field_validator("score_weights")
    @classmethod
    def validate_weights(cls, v):
        if v is None:
            return v
        valid_keys = {"embedding", "salary", "location", "recency", "llm", "language"}
        if not set(v.keys()).issubset(valid_keys):
            raise ValueError(f"Invalid weight keys. Allowed: {valid_keys}")
        if any(not (0 <= val <= 1) for val in v.values()):
            raise ValueError("Each weight must be between 0 and 1")
        total = sum(v.values())
        if not (0.99 <= total <= 1.01):
            raise ValueError(f"Weights must sum to 1.0, got {total}")
        return v

    @field_validator("skills", "languages", "locations")
    @classmethod
    def validate_list_items(cls, v):
        if v is None:
            return v
        if len(v) > 50:
            raise ValueError("Maximum 50 items allowed")
        return [item.strip() for item in v if item.strip()]


class ProfileResponse(BaseModel):
    """Full profile read response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    title: str | None
    skills: list
    experience_years: int | None
    languages: list
    locations: list
    salary_min: int | None
    salary_max: int | None
    remote_pref: str
    cv_text: str | None
    has_cv_embedding: bool = False
    score_weights: dict | None
    watchlist_schools_enabled: bool = False
    updated_at: datetime

    _null_json_as_empty = field_validator(
        "skills", "languages", "locations", mode="before"
    )(_empty_list_if_null)


class CVUploadResponse(BaseModel):
    """Response for POST /api/v1/profile/cv."""

    message: str
    cv_text_length: int
    skills_extracted: list[str]
    embedding_task_id: str | None = None


class CVDeleteResponse(BaseModel):
    """Response for DELETE /api/v1/profile/cv."""

    message: str


class UserExport(BaseModel):
    """Full user data export for GDPR portability."""

    id: uuid.UUID
    email: str
    is_active: bool
    plan: str
    created_at: datetime
    last_login: datetime | None
    gdpr_consent: bool
    gdpr_consent_at: datetime | None
    profile: ProfileData | None
    exported_at: datetime


class DeleteAccountRequest(BaseModel):
    """Request body for account deletion — requires password confirmation."""

    password: str = Field(..., min_length=1)


class DeleteConfirmation(BaseModel):
    message: str
    user_id: uuid.UUID
    deleted_at: datetime
