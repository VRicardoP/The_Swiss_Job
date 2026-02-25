"""Pydantic schemas for Job entities."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class JobBase(BaseModel):
    """Fields common to creation and response."""

    title: str
    company: str
    location: str | None = None
    canton: str | None = None
    description: str | None = None
    description_snippet: str | None = None
    url: str
    salary_min_chf: int | None = None
    salary_max_chf: int | None = None
    salary_original: str | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    language: str | None = None
    seniority: str | None = None
    contract_type: str | None = None
    remote: bool = False
    tags: list[str] = Field(default_factory=list)
    logo: str | None = None
    employment_type: str | None = None


class JobCreate(JobBase):
    """Schema for creating a job (used internally by providers)."""

    hash: str
    source: str


class JobResponse(JobBase):
    """Schema for job API responses."""

    model_config = ConfigDict(from_attributes=True)

    hash: str
    source: str
    first_seen_at: datetime
    last_seen_at: datetime
    is_active: bool
    fuzzy_hash: str | None = None
    duplicate_of: str | None = None


class JobBrief(BaseModel):
    """Lightweight schema for search results / listing."""

    model_config = ConfigDict(from_attributes=True)

    hash: str
    title: str
    company: str
    location: str | None = None
    canton: str | None = None
    description_snippet: str | None = None
    url: str
    salary_min_chf: int | None = None
    salary_max_chf: int | None = None
    remote: bool = False
    language: str | None = None
    seniority: str | None = None
    contract_type: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: str
    logo: str | None = None
    first_seen_at: datetime
    is_active: bool


class JobSearchResponse(BaseModel):
    """Paginated search response."""

    data: list[JobBrief]
    total: int
    limit: int
    offset: int
    has_more: bool


class SourceInfo(BaseModel):
    """Active source summary."""

    name: str
    count: int
    last_seen: datetime | None = None


class SalaryStats(BaseModel):
    """Salary distribution statistics."""

    min: int | None = None
    max: int | None = None
    median: float | None = None
    mean: float | None = None


class JobStats(BaseModel):
    """Aggregated job statistics."""

    total_jobs: int
    by_source: dict[str, int]
    by_canton: dict[str, int]
    by_language: dict[str, int]
    by_seniority: dict[str, int]
    by_contract: dict[str, int]
    salary_stats: SalaryStats
