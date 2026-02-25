"""Celery tasks: maintenance operations (dedup, URL health, cleanup)."""

import logging

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.dedup_semantic_batch")
def dedup_semantic_batch() -> dict:
    """Semantic deduplication via embedding cosine similarity.

    Full implementation in Fase 2 (AI Matching pipeline).
    Uses pgvector cosine distance with threshold > 0.95.
    """
    logger.info("Semantic dedup batch: not yet implemented (Fase 2)")
    return {"status": "not_implemented"}


@celery_app.task(name="tasks.check_job_urls")
def check_job_urls() -> dict:
    """Verify job URLs are still active (HEAD request health check).

    Full implementation in Fase 1 Week 3.
    Marks jobs as inactive if 404/410/timeout.
    """
    logger.info("URL health check: not yet implemented (Fase 1 Week 3)")
    return {"status": "not_implemented"}
