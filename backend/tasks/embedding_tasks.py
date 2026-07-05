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
    soft_time_limit=150,
    time_limit=180,
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

        embedding = await asyncio.to_thread(matcher.encode, combined_text)
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
    soft_time_limit=150,
    time_limit=180,
)
def generate_job_embeddings(self, batch_size: int = 100) -> dict[str, Any]:
    """Generate embeddings for jobs without one (un solo lote, flujo intervalos)."""
    try:
        return asyncio.run(_generate_job_embeddings_async(batch_size))
    except Exception as exc:
        logger.error("generate_job_embeddings failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(
    name="tasks.ai.embed_all_pending",
    bind=True,
    max_retries=1,
    soft_time_limit=1800,
    time_limit=2100,
)
def embed_all_pending(self, batch_size: int = 200) -> dict[str, Any]:
    """Genera embeddings para TODOS los jobs pendientes, drenando en bucle.

    Usado por la cosecha diaria: garantiza que cada oferta nueva tenga embedding
    antes de que corra el matching. El modelo es LOCAL (sentence-transformers),
    así que procesar cientos de ofertas no consume crédito de ninguna API de IA.
    """
    try:
        return asyncio.run(_embed_all_pending_async(batch_size))
    except Exception as exc:
        logger.error("embed_all_pending failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _embed_pending_batch(db, matcher, batch_size: int) -> int:
    """Codifica un lote de jobs sin embedding y hace commit. Devuelve cuántos.

    `build_job_text` es un staticmethod de JobMatcher; se invoca desde la
    instancia para no reimportar la clase en el bucle.
    """
    from sqlalchemy import select

    from models.job import Job

    stmt = (
        select(Job)
        .where(
            Job.is_active.is_(True),
            Job.duplicate_of.is_(None),
            Job.embedding.is_(None),
        )
        .limit(batch_size)
    )
    jobs = (await db.execute(stmt)).scalars().all()
    if not jobs:
        return 0

    texts = [
        matcher.build_job_text(
            {
                "title": j.title,
                "company": j.company,
                "description": j.description or "",
                "tags": j.tags or [],
            }
        )
        for j in jobs
    ]
    embeddings = await asyncio.to_thread(matcher.encode_batch, texts)
    for job, emb in zip(jobs, embeddings):
        job.embedding = emb.tolist()
    await db.commit()
    return len(jobs)


async def _generate_job_embeddings_async(batch_size: int) -> dict[str, Any]:
    """Un solo lote (flujo por intervalos). Encadena dedup semántico si hubo trabajo."""
    from database import task_session
    from services.job_matcher import JobMatcher

    matcher = JobMatcher()
    async with task_session() as db:
        processed = await _embed_pending_batch(db, matcher, batch_size)

    if processed:
        from tasks.maintenance_tasks import dedup_semantic_batch

        dedup_semantic_batch.delay(batch_size=200)
        logger.info(
            "Generated embeddings for %d jobs, dispatched semantic dedup", processed
        )
    return {"status": "success", "processed": processed}


async def _embed_all_pending_async(batch_size: int) -> dict[str, Any]:
    """Drena TODOS los pendientes en bucle; dispara dedup una sola vez al final."""
    from database import task_session
    from services.job_matcher import JobMatcher

    matcher = JobMatcher()
    total = 0
    async with task_session() as db:
        while True:
            n = await _embed_pending_batch(db, matcher, batch_size)
            total += n
            if n < batch_size:  # último lote incompleto → no quedan pendientes
                break

    if total:
        from tasks.maintenance_tasks import dedup_semantic_batch

        dedup_semantic_batch.delay(batch_size=min(max(total, 200), 1000))
    logger.info("embed_all_pending: %d jobs embedded", total)
    return {"status": "success", "processed": total}
