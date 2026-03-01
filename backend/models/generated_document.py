"""GeneratedDocument model — stores AI-generated CV and cover letter text."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class GeneratedDocument(Base):
    __tablename__ = "generated_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_hash: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("jobs.hash", ondelete="CASCADE"),
        nullable=False,
    )
    doc_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,  # "cv" or "cover_letter"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(
        String(5),
        nullable=True,  # "en", "de", "fr", "it"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
