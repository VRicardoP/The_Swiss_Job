import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
    updated_at: datetime


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


class DeleteConfirmation(BaseModel):
    message: str
    user_id: uuid.UUID
    deleted_at: datetime
