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


@celery_app.task(name="tasks.cleanup_stale_jobs")
def cleanup_stale_jobs(max_age_days: int = 60) -> dict[str, Any]:
    """Elimina ofertas de empleo que superan el umbral de antigüedad.

    Política: 60 días desde `last_seen_at` (última vez visto en el feed).
    Las ofertas no vistas en 60 días se consideran caducadas y se eliminan.
    """
    try:
        return asyncio.run(_cleanup_stale_jobs_async(max_age_days))
    except Exception as exc:
        logger.error("cleanup_stale_jobs failed: %s", exc)
        return {"status": "error", "error": str(exc)}


async def _cleanup_stale_jobs_async(max_age_days: int) -> dict[str, Any]:
    """Async: borra jobs caducados según política de retención por categoría.

    Política de retención:
    - Normal (sin interacción): max_age_days (por defecto 60 días)
    - Guardadas como Good (thumbs_up/applied): 90 días desde last_seen_at
    - En pipeline de candidaturas (job_applications): 180 días desde last_seen_at
    """
    from sqlalchemy import text

    from database import task_session

    async with task_session() as db:
        # 1. Borrar jobs normales caducados (excluir los que tienen retención extendida)
        r_normal = await db.execute(
            text("""
                DELETE FROM jobs
                WHERE last_seen_at < NOW() - make_interval(days => :days)
                  AND hash NOT IN (
                      SELECT DISTINCT job_hash FROM match_results
                      WHERE feedback IN ('thumbs_up', 'applied')
                  )
                  AND hash NOT IN (
                      SELECT DISTINCT job_hash FROM job_applications
                  )
            """),
            {"days": max_age_days},
        )

        # 2. Borrar jobs guardados como Good con más de 90 días
        #    (que además no estén en pipeline)
        r_good = await db.execute(
            text("""
                DELETE FROM jobs
                WHERE last_seen_at < NOW() - INTERVAL '90 days'
                  AND hash IN (
                      SELECT DISTINCT job_hash FROM match_results
                      WHERE feedback IN ('thumbs_up', 'applied')
                  )
                  AND hash NOT IN (
                      SELECT DISTINCT job_hash FROM job_applications
                  )
            """),
        )

        # 3. Borrar jobs en pipeline con más de 180 días
        r_pipeline = await db.execute(
            text("""
                DELETE FROM jobs
                WHERE last_seen_at < NOW() - INTERVAL '180 days'
                  AND hash IN (
                      SELECT DISTINCT job_hash FROM job_applications
                  )
            """),
        )

        await db.commit()

    deleted_normal = r_normal.rowcount
    deleted_good = r_good.rowcount
    deleted_pipeline = r_pipeline.rowcount
    total = deleted_normal + deleted_good + deleted_pipeline

    logger.info(
        "cleanup_stale_jobs: %d eliminadas en total "
        "(normales >%dd: %d | good >90d: %d | pipeline >180d: %d)",
        total, max_age_days, deleted_normal, deleted_good, deleted_pipeline,
    )
    return {
        "status": "success",
        "deleted_total": total,
        "deleted_normal": deleted_normal,
        "deleted_good": deleted_good,
        "deleted_pipeline": deleted_pipeline,
        "max_age_days": max_age_days,
    }
