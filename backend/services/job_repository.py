"""JobRepository — database operations for job upsert and dedup management."""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job

logger = logging.getLogger(__name__)


class JobRepository:
    """Encapsulates all DB operations for jobs."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_job(self, job_dict: dict) -> bool:
        """Insert a new job or update last_seen_at if it already exists.

        Uses PostgreSQL INSERT ... ON CONFLICT for atomicity.
        Returns True if the job is new (inserted), False if updated.
        """
        # Filter to only include columns that exist on the Job model
        valid_columns = {c.key for c in Job.__table__.columns}
        values = {k: v for k, v in job_dict.items() if k in valid_columns}

        stmt = pg_insert(Job).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["hash"],
            set_={
                "last_seen_at": datetime.now(timezone.utc),
                "is_active": True,
            },
        ).returning(
            # xmax = 0 means a fresh insert (no previous row version)
            Job.__table__.c.hash,
            func.current_setting("server_version").label("_sv"),  # dummy
        )

        # Use a simpler approach: check if hash exists before insert
        existing = await self.db.execute(
            select(Job.hash).where(Job.hash == values["hash"])
        )
        exists = existing.scalar_one_or_none() is not None

        await self.db.execute(
            pg_insert(Job)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["hash"],
                set_={
                    "last_seen_at": datetime.now(timezone.utc),
                    "is_active": True,
                },
            )
        )

        return not exists

    async def mark_duplicate(self, job_hash: str, canonical_hash: str) -> None:
        """Mark a job as a duplicate of another (deactivate it)."""
        await self.db.execute(
            update(Job)
            .where(Job.hash == job_hash)
            .values(duplicate_of=canonical_hash, is_active=False)
        )

    async def get_active_count(self) -> int:
        """Count active, non-duplicate jobs."""
        result = await self.db.execute(
            select(func.count()).select_from(Job).where(Job.is_active.is_(True))
        )
        return result.scalar_one()
