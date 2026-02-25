from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.source_compliance import SourceCompliance

# Block threshold: after this many consecutive blocks, auto-disable the source
BLOCK_THRESHOLD = 3


class ComplianceEngine:
    """Verifies TOS compliance before scraping. Automatic kill-switch."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def can_scrape(self, source_key: str) -> bool:
        """Check is_allowed + robots_txt_ok for a source."""
        result = await self.db.execute(
            select(SourceCompliance).where(
                SourceCompliance.source_key == source_key
            )
        )
        source = result.scalar_one_or_none()
        if source is None:
            return False
        return source.is_allowed and source.robots_txt_ok

    async def report_block(self, source_key: str, status_code: int) -> None:
        """Record a block event. If N consecutive blocks → kill-switch."""
        result = await self.db.execute(
            select(SourceCompliance).where(
                SourceCompliance.source_key == source_key
            )
        )
        source = result.scalar_one_or_none()
        if source is None:
            return

        now = datetime.now(timezone.utc)
        source.consecutive_blocks += 1
        source.last_blocked_at = now

        if (
            source.auto_disable_on_block
            and source.consecutive_blocks >= BLOCK_THRESHOLD
        ):
            source.is_allowed = False

        await self.db.commit()

    async def reset_blocks(self, source_key: str) -> None:
        """Reset consecutive block counter after a successful request."""
        await self.db.execute(
            update(SourceCompliance)
            .where(SourceCompliance.source_key == source_key)
            .values(consecutive_blocks=0)
        )
        await self.db.commit()

    async def get_compliance_status(self) -> list[dict]:
        """Return status of all sources for admin panel."""
        result = await self.db.execute(
            select(SourceCompliance).order_by(SourceCompliance.source_key)
        )
        sources = result.scalars().all()
        return [
            {
                "source_key": s.source_key,
                "method": s.method,
                "is_allowed": s.is_allowed,
                "robots_txt_ok": s.robots_txt_ok,
                "rate_limit_seconds": s.rate_limit_seconds,
                "max_requests_per_hour": s.max_requests_per_hour,
                "consecutive_blocks": s.consecutive_blocks,
                "last_blocked_at": s.last_blocked_at.isoformat()
                if s.last_blocked_at
                else None,
                "tos_reviewed_at": s.tos_reviewed_at.isoformat()
                if s.tos_reviewed_at
                else None,
                "tos_notes": s.tos_notes,
            }
            for s in sources
        ]
