"""Celery tasks: maintenance operations (dedup, URL health, cleanup)."""

import asyncio
import logging
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.dedup_semantic_batch")
def dedup_semantic_batch(batch_size: int = 200) -> dict[str, Any]:
    """Semantic deduplication via embedding cosine similarity.

    Processes active jobs with embeddings, finds duplicates with cosine > 0.95.
    """
    try:
        return asyncio.run(_dedup_semantic_batch_async(batch_size))
    except Exception as exc:
        logger.error("dedup_semantic_batch failed: %s", exc)
        return {"status": "error", "error": str(exc)}


async def _dedup_semantic_batch_async(batch_size: int) -> dict[str, Any]:
    """Async implementation: find and mark semantic duplicates."""
    from sqlalchemy import select

    from config import settings
    from database import task_session
    from models.job import Job
    from services.deduplicator import Deduplicator
    from services.job_repository import JobRepository

    async with task_session() as db:
        # Get active jobs with embeddings that are not already duplicates
        stmt = (
            select(Job)
            .where(
                Job.is_active.is_(True),
                Job.duplicate_of.is_(None),
                Job.embedding.is_not(None),
            )
            .order_by(Job.first_seen_at.desc())
            .limit(batch_size)
        )
        result = await db.execute(stmt)
        jobs = result.scalars().all()

        if not jobs:
            return {"status": "success", "processed": 0, "duplicates_found": 0}

        repo = JobRepository(db)
        dupes_found = 0

        for job in jobs:
            canonical_hashes = await Deduplicator.find_semantic_duplicates(
                db, job, threshold=settings.SEMANTIC_DEDUP_THRESHOLD
            )
            if canonical_hashes:
                await repo.mark_duplicate(job.hash, canonical_hashes[0])
                dupes_found += 1

        await db.commit()

        logger.info(
            "Semantic dedup: processed %d jobs, found %d duplicates",
            len(jobs),
            dupes_found,
        )
        return {
            "status": "success",
            "processed": len(jobs),
            "duplicates_found": dupes_found,
        }


@celery_app.task(name="tasks.check_job_urls")
def check_job_urls() -> dict:
    """Verify job URLs are still active (HEAD request health check).

    Full implementation in Fase 1 Week 3.
    Marks jobs as inactive if 404/410/timeout.
    """
    logger.info("URL health check: not yet implemented (Fase 1 Week 3)")
    return {"status": "not_implemented"}
