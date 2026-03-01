"""Celery task: fetch jobs from all providers, normalize, dedup, and store."""

import asyncio
import logging
from typing import Any

from celery_app import celery_app
from config import settings
from database import task_session
from providers import get_all_providers
from services.data_normalizer import DataNormalizer
from services.deduplicator import Deduplicator
from services.job_repository import JobRepository

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.fetch_providers",
    bind=True,
    max_retries=2,
    soft_time_limit=540,
    time_limit=600,
)
def fetch_providers(self) -> dict[str, Any]:
    """Fetch jobs from all enabled providers, normalize, dedup, and store.

    This is the main data ingestion pipeline, dispatched by APScheduler.
    Celery tasks must be synchronous — async work runs via asyncio.run().
    """
    try:
        result = asyncio.run(_fetch_providers_async())

        # Chain: generate embeddings for newly ingested jobs
        if result.get("new", 0) > 0:
            from tasks.embedding_tasks import generate_job_embeddings

            generate_job_embeddings.delay(batch_size=100)
            logger.info(
                "Dispatched generate_job_embeddings for %d new jobs", result["new"]
            )

        return result
    except Exception as exc:
        logger.error("fetch_providers failed: %s", exc)
        raise self.retry(exc=exc, countdown=300)


async def _fetch_providers_async() -> dict[str, Any]:
    """Async implementation of the fetch pipeline.

    Phase 1: Parallel HTTP fetch with semaphore (TD-18).
    Phase 2: Sequential DB persist (savepoints can't interleave on same session).
    """
    providers = get_all_providers()
    summary: dict[str, Any] = {
        "providers": 0,
        "fetched": 0,
        "new": 0,
        "updated": 0,
        "dupes": 0,
        "errors": 0,
    }

    # Phase 1: parallel fetch
    sem = asyncio.Semaphore(settings.FETCH_CONCURRENCY)

    async def _fetch_one(provider):
        source = provider.get_source_name()
        async with sem:
            try:
                jobs = await provider.fetch_jobs("software developer", "Switzerland")
                logger.info("Provider %s returned %d jobs", source, len(jobs))
                return source, jobs
            except Exception as e:
                logger.error("Provider %s fetch failed: %s", source, e)
                return source, None

    fetch_results = await asyncio.gather(*[_fetch_one(p) for p in providers])

    # Phase 2: sequential DB persist
    async with task_session() as db:
        repo = JobRepository(db)

        for source, jobs in fetch_results:
            if jobs is None:
                summary["errors"] += 1
                continue

            try:
                for job in jobs:
                    try:
                        async with db.begin_nested():
                            job = DataNormalizer.normalize(job)

                            job["fuzzy_hash"] = Deduplicator.compute_fuzzy_hash(
                                job["title"], job["company"]
                            )

                            is_new = await repo.upsert_job(job)

                            if is_new:
                                canonical = await Deduplicator.find_fuzzy_duplicate(
                                    db, job["fuzzy_hash"], job["source"]
                                )
                                if canonical:
                                    await repo.mark_duplicate(job["hash"], canonical)
                                    summary["dupes"] += 1
                                else:
                                    summary["new"] += 1
                            else:
                                summary["updated"] += 1

                            summary["fetched"] += 1

                    except Exception as e:
                        summary["errors"] += 1
                        logger.error("Error processing job from %s: %s", source, e)

                await db.commit()
                summary["providers"] += 1

            except Exception as e:
                await db.rollback()
                summary["errors"] += 1
                logger.error("Provider %s persist failed: %s", source, e)

    logger.info("Fetch complete: %s", summary)
    return summary
