"""Latest profile snapshot; source mutations queue it transactionally via triggers."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ProfileSyncState(Base):
    __tablename__ = "profile_sync_state"
    __table_args__ = (
        CheckConstraint(
            "version > 0 AND delivered_version >= 0 AND delivered_version <= version",
            name="ck_profile_sync_version",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivered_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    content: Mapped[dict | None] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
