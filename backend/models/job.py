"""Job model — central data entity for aggregated job listings."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.enums import ContractType, SalaryPeriod, Seniority


class Job(Base):
    __tablename__ = "jobs"

    # Primary key — MD5(title+company+url)
    hash: Mapped[str] = mapped_column(String(32), primary_key=True)

    # Source provider
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Core fields
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    location: Mapped[str | None] = mapped_column(String(300))
    canton: Mapped[str | None] = mapped_column(String(2), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    description_snippet: Mapped[str | None] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(
        String(2048), unique=True, nullable=False, index=True
    )

    # Salary (normalized to CHF annual by DataNormalizer in Week 2)
    salary_min_chf: Mapped[int | None] = mapped_column(Integer)
    salary_max_chf: Mapped[int | None] = mapped_column(Integer)
    salary_original: Mapped[str | None] = mapped_column(String(200))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_period: Mapped[SalaryPeriod | None] = mapped_column(
        ENUM(SalaryPeriod, name="salaryperiod", create_type=True)
    )

    # Classification (populated by DataNormalizer in Week 2)
    language: Mapped[str | None] = mapped_column(String(5))
    seniority: Mapped[Seniority | None] = mapped_column(
        ENUM(Seniority, name="seniority", create_type=True)
    )
    contract_type: Mapped[ContractType | None] = mapped_column(
        ENUM(ContractType, name="contracttype", create_type=True)
    )

    # Flags & tags
    remote: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # AI embedding (paraphrase-multilingual-MiniLM-L12-v2, 384 dims)
    embedding = mapped_column(Vector(384), nullable=True)

    # Extra metadata
    logo: Mapped[str | None] = mapped_column(String(2048))
    employment_type: Mapped[str | None] = mapped_column(String(100))

    # Timestamps
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )
    url_last_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Deduplication
    fuzzy_hash: Mapped[str | None] = mapped_column(String(32), index=True)
    duplicate_of: Mapped[str | None] = mapped_column(String(32))

    def __repr__(self) -> str:
        return f"<Job hash={self.hash} source={self.source} title={self.title!r}>"
