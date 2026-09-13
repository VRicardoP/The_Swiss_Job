"""School wire contract: public identity, consumer configuration, private state."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SchoolSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=200)
    country: str = Field(default="CH", min_length=2, max_length=2)
    city: str | None = Field(default=None, max_length=200)
    group_tier: Literal["A", "B", "C"]
    monitoring_mode: Literal["scrape", "manual_only"]
    policy: Literal[
        "portal_only",
        "direct_email_ok",
        "direct_email_director",
        "direct_email",
        "portal_nord_anglia",
        "portal_workday",
        "portal_successfactors",
        "manual",
    ]
    contact_name: str | None = Field(default=None, max_length=200)
    contact_email: str | None = Field(default=None, max_length=200)
    portal_url: str | None = Field(default=None, max_length=2000)
    jobs_page_url: str | None = Field(default=None, max_length=2000)
    template_letter: Literal["A", "B"] | None = None
    scraping_method: str | None = Field(default=None, max_length=50)
    scraping_params: dict | None = None
    manual_reminder_interval_days: int | None = Field(default=None, ge=1, le=365)
    is_active: bool = False
    notes: str | None = Field(default=None, max_length=65536)

    @field_validator("portal_url", "jobs_page_url")
    @classmethod
    def http_url(cls, value):
        if value and not value.strip().lower().startswith(("https://", "http://")):
            raise ValueError("URL must start with http:// or https://")
        return value


class MonitorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    external_ref: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$")
    settings: SchoolSettings


class MonitorDTO(BaseModel):
    id: UUID
    school_id: UUID
    external_ref: str
    settings: SchoolSettings
    version: int
    created_at: datetime
    updated_at: datetime


class MonitorsPage(BaseModel):
    items: list[MonitorDTO]
    next_cursor: str | None


class SchoolPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
