"""MatchResult model — persists AI match scores between users and jobs."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class MatchResult(Base):
    __tablename__ = "match_results"
    __table_args__ = (
        UniqueConstraint("user_id", "job_hash", name="uq_match_user_job"),
    )

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
        index=True,
    )

    # Individual factor scores [0.0, 1.0]
    score_embedding: Mapped[float] = mapped_column(Float, nullable=False)
    score_salary: Mapped[float] = mapped_column(Float, nullable=False)
    score_location: Mapped[float] = mapped_column(Float, nullable=False)
    score_recency: Mapped[float] = mapped_column(Float, nullable=False)
    score_llm: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Weighted final score [0, 100]
    score_final: Mapped[float] = mapped_column(Float, nullable=False, index=True)

    # LLM-generated explanation
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Skill analysis
    matching_skills: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    missing_skills: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    # User feedback (thumbs_up, thumbs_down, applied, dismissed)
    feedback: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Implicit feedback signals (list of {action, duration_ms?})
    feedback_implicit: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<MatchResult user={self.user_id} job={self.job_hash} "
            f"score={self.score_final}>"
        )
