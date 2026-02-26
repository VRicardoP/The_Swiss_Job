"""Celery tasks: embedding generation for profiles and jobs."""

import asyncio
import logging
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.ai.generate_profile_embedding",
    bind=True,
    max_retries=2,
)
def generate_profile_embedding(self, user_id: str) -> dict[str, Any]:
    """Generate embedding for a user's CV text and store it."""
    try:
        return asyncio.run(_generate_profile_embedding_async(user_id))
    except Exception as exc:
        logger.error("generate_profile_embedding failed for %s: %s", user_id, exc)
        raise self.retry(exc=exc, countdown=60)


async def _generate_profile_embedding_async(user_id: str) -> dict[str, Any]:
    """Async implementation: load cv_text, encode, store cv_embedding."""
    import uuid as uuid_mod

    from sqlalchemy import select

    from database import task_session
    from models.user_profile import UserProfile
    from services.job_matcher import JobMatcher

    uid = uuid_mod.UUID(user_id)
    matcher = JobMatcher()

    async with task_session() as db:
        result = await db.execute(select(UserProfile).where(UserProfile.user_id == uid))
        profile = result.scalar_one_or_none()

        if profile is None:
            logger.warning("Profile not found for user %s", user_id)
            return {"status": "error", "reason": "profile_not_found"}

        if not profile.cv_text:
            logger.warning("No CV text for user %s", user_id)
            return {"status": "error", "reason": "no_cv_text"}

        # Build text: combine CV with profile metadata for richer embedding
        parts = [profile.cv_text]
        if profile.title:
            parts.insert(0, profile.title)
        if profile.skills:
            parts.append(" ".join(profile.skills))
        combined_text = " ".join(parts)

        embedding = matcher.encode(combined_text)
        profile.cv_embedding = embedding.tolist()
        await db.commit()

        logger.info(
            "Generated embedding for user %s (%d dims)",
            user_id,
            len(embedding),
        )
        return {
            "status": "success",
            "user_id": user_id,
            "embedding_dims": len(embedding),
        }


@celery_app.task(
    name="tasks.ai.generate_job_embeddings",
    bind=True,
    max_retries=2,
)
def generate_job_embeddings(self, batch_size: int = 100) -> dict[str, Any]:
    """Generate embeddings for jobs that don't have one yet."""
    try:
        return asyncio.run(_generate_job_embeddings_async(batch_size))
    except Exception as exc:
        logger.error("generate_job_embeddings failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _generate_job_embeddings_async(batch_size: int) -> dict[str, Any]:
    """Async implementation: batch encode jobs without embeddings."""
    from sqlalchemy import select

    from database import task_session
    from models.job import Job
    from services.job_matcher import JobMatcher

    matcher = JobMatcher()

    async with task_session() as db:
        stmt = (
            select(Job)
            .where(
                Job.is_active.is_(True),
                Job.duplicate_of.is_(None),
                Job.embedding.is_(None),
            )
            .limit(batch_size)
        )
        result = await db.execute(stmt)
        jobs = result.scalars().all()

        if not jobs:
            return {"status": "success", "processed": 0}

        texts = [
            JobMatcher.build_job_text(
                {
                    "title": j.title,
                    "company": j.company,
                    "description": j.description or "",
                    "tags": j.tags or [],
                }
            )
            for j in jobs
        ]

        embeddings = matcher.encode_batch(texts)

        for job, emb in zip(jobs, embeddings):
            job.embedding = emb.tolist()

        await db.commit()

        # Chain: run semantic dedup after embedding generation
        from tasks.maintenance_tasks import dedup_semantic_batch

        dedup_semantic_batch.delay(batch_size=200)
        logger.info(
            "Generated embeddings for %d jobs, dispatched semantic dedup", len(jobs)
        )
        return {"status": "success", "processed": len(jobs)}
