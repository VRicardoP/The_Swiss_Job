"""Latest durable exclusion snapshot awaiting delivery to the core."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class ExclusionSyncState(Base):
    __tablename__ = "exclusion_sync_state"
    __table_args__ = (
        CheckConstraint(
            "version > 0 AND delivered_version >= 0 AND delivered_version <= version",
            name="ck_exclusion_delivery_version",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delivered_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    exclusions: Mapped[list] = mapped_column(JSONB, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
