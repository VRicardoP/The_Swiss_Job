"""Celery task: fetch jobs from all providers, normalize, dedup, and store."""

import asyncio
import logging
from typing import Any

from celery_app import celery_app
from database import async_session
from providers import get_all_providers
from services.data_normalizer import DataNormalizer
from services.deduplicator import Deduplicator
from services.job_repository import JobRepository

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.fetch_providers", bind=True, max_retries=2)
def fetch_providers(self) -> dict[str, Any]:
    """Fetch jobs from all enabled providers, normalize, dedup, and store.

    This is the main data ingestion pipeline, dispatched by APScheduler.
    Celery tasks must be synchronous — async work runs via asyncio.run().
    """
    try:
        return asyncio.run(_fetch_providers_async())
    except Exception as exc:
        logger.error("fetch_providers failed: %s", exc)
        raise self.retry(exc=exc, countdown=300)


async def _fetch_providers_async() -> dict[str, Any]:
    """Async implementation of the fetch pipeline."""
    providers = get_all_providers()
    summary: dict[str, Any] = {
        "providers": 0,
        "fetched": 0,
        "new": 0,
        "updated": 0,
        "dupes": 0,
        "errors": 0,
    }

    async with async_session() as db:
        repo = JobRepository(db)

        for provider in providers:
            source = provider.get_source_name()
            try:
                jobs = await provider.fetch_jobs("software developer", "Switzerland")
                logger.info("Provider %s returned %d jobs", source, len(jobs))

                for job in jobs:
                    try:
                        # Enrich with DataNormalizer
                        job = DataNormalizer.normalize(job)

                        # Compute fuzzy hash for cross-source dedup
                        job["fuzzy_hash"] = Deduplicator.compute_fuzzy_hash(
                            job["title"], job["company"]
                        )

                        # Upsert (exact dedup via ON CONFLICT on hash)
                        is_new = await repo.upsert_job(job)
                        summary["fetched"] += 1

                        if is_new:
                            # Check for cross-source fuzzy duplicate
                            # Use job["source"] (stored in DB) not provider name
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

                    except (KeyError, ValueError, TypeError) as e:
                        summary["errors"] += 1
                        logger.error("Error processing job from %s: %s", source, e)

                summary["providers"] += 1

            except Exception as e:
                summary["errors"] += 1
                logger.error("Provider %s failed: %s", source, e)

        await db.commit()

    logger.info("Fetch complete: %s", summary)
    return summary
