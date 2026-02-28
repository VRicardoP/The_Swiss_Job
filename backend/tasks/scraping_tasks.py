"""Celery task: fetch jobs from all scrapers, normalize, dedup, and store.

Follows the same per-job savepoint pipeline as fetch_tasks.py but runs
on a separate schedule (every 6h vs 30min for API providers).
"""

import asyncio
import logging
from typing import Any

from celery_app import celery_app
from database import task_session
from services.data_normalizer import DataNormalizer
from services.deduplicator import Deduplicator
from services.job_repository import JobRepository

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.scraping.fetch_scrapers",
    bind=True,
    max_retries=1,
    soft_time_limit=1800,
    time_limit=2400,
)
def fetch_scrapers(self) -> dict[str, Any]:
    """Fetch jobs from all enabled scrapers."""
    try:
        result = asyncio.run(_fetch_scrapers_async())

        if result.get("new", 0) > 0:
            from tasks.embedding_tasks import generate_job_embeddings

            generate_job_embeddings.delay(batch_size=100)

        return result
    except Exception as exc:
        logger.error("fetch_scrapers failed: %s", exc)
        raise self.retry(exc=exc, countdown=600)


async def _fetch_scrapers_async() -> dict[str, Any]:
    """Async implementation — sequential scraper execution."""
    from scrapers import get_all_scrapers

    scrapers = get_all_scrapers()
    summary = {
        "scrapers": 0,
        "fetched": 0,
        "new": 0,
        "updated": 0,
        "dupes": 0,
        "errors": 0,
    }

    async with task_session() as db:
        repo = JobRepository(db)

        for scraper in scrapers:
            source = scraper.get_source_name()
            try:
                jobs = await scraper.fetch_jobs("", "Switzerland")
                logger.info("Scraper %s returned %d jobs", source, len(jobs))

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
                        logger.error(
                            "Error processing scraped job from %s: %s",
                            source,
                            e,
                        )

                await db.commit()
                summary["scrapers"] += 1

            except Exception as e:
                await db.rollback()
                summary["errors"] += 1
                logger.error("Scraper %s failed: %s", source, e)

    logger.info("Scraper fetch complete: %s", summary)
    return summary
