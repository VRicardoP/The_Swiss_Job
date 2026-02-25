import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SourceCompliance(Base):
    __tablename__ = "source_compliance"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_key: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "api" | "scraping"
    is_allowed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rate_limit_seconds: Mapped[float] = mapped_column(
        Float, default=2.0, nullable=False
    )
    robots_txt_ok: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tos_reviewed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    tos_notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    max_requests_per_hour: Mapped[int] = mapped_column(
        Integer, default=120, nullable=False
    )
    last_blocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auto_disable_on_block: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    consecutive_blocks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<SourceCompliance {self.source_key} allowed={self.is_allowed}>"
