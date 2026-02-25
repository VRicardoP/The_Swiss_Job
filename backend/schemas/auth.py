import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    gdpr_consent: bool

    @field_validator("gdpr_consent")
    @classmethod
    def gdpr_must_be_true(cls, v: bool) -> bool:
        if not v:
            raise ValueError("GDPR consent is required to create an account")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenRefresh(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    is_active: bool
    plan: str
    created_at: datetime
    last_login: datetime | None
    gdpr_consent: bool
    gdpr_consent_at: datetime | None


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse
