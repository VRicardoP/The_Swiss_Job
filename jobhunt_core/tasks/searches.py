"""Saved searches: one transaction per aggregate, fair retries, no external I/O."""

import asyncio
import logging
import uuid

import sqlalchemy as sa
from billiard.exceptions import SoftTimeLimitExceeded

from jobhunt_core.celery_app import celery_app
from jobhunt_core.config import settings
from jobhunt_core.database import task_session_factory
from jobhunt_core import search_execution

logger = logging.getLogger(__name__)


@celery_app.task(name="jobhunt.searches.run_due", bind=True, max_retries=1)
def run_due_task(self, limit: int = 100):
    try:
        return asyncio.run(_run(limit=limit))
    except Exception as exc:
        # Never log query text or Pydantic input values (personal preferences).
        logger.error("saved-search sweep failed: %s", type(exc).__name__)
        raise self.retry(exc=RuntimeError("saved-search sweep failed"), countdown=120) from None


@celery_app.task(name="jobhunt.searches.run_one", bind=True, max_retries=1)
def run_one_task(self, search_id: str):
    if not settings.CORE_SAVED_SEARCH_EXECUTION_ENABLED:
        return {"status": "disabled", "processed": 0, "matches": 0, "failed": 0}
    sid = uuid.UUID(search_id)
    try:
        result = asyncio.run(_run(search_id=sid))
    except Exception:
        raise self.retry(exc=RuntimeError("saved-search execution failed"), countdown=120) from None
    if result["failed"]:
        raise self.retry(exc=RuntimeError("saved-search execution failed"), countdown=120)
    return result


async def _run(*, limit=100, search_id=None, session_factory=None):
    if not settings.CORE_SAVED_SEARCH_EXECUTION_ENABLED:
        return {"status": "disabled", "processed": 0, "matches": 0, "failed": 0}
    if session_factory is None:
        async with task_session_factory() as factory:
            return await _run(limit=limit, search_id=search_id, session_factory=factory)
    destinations = set(settings.CORE_DELIVERY_HTTP_DESTINATIONS)
    if not destinations:
        raise ValueError("saved-search execution requires a real HTTP inbox")
    if search_id is None:
        async with session_factory() as db:
            ids = await search_execution.due_search_ids(db, limit=limit)
    else:
        ids = [search_id]
    processed = matches = failed = 0
    for sid in ids:
        try:
            async with session_factory() as db:
                await db.execute(sa.text("SET LOCAL lock_timeout='5s'"))
                await db.execute(sa.text("SET LOCAL statement_timeout='60s'"))
                result = await search_execution.execute_search(
                    db, sid, destinations=destinations, force=search_id is not None,
                )
                await db.commit()
            if result["status"] == "ok":
                processed += 1
                matches += result["matches"]
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            failed += 1
            logger.error("saved-search %s failed: %s", sid, type(exc).__name__)
            # The failed transaction is closed before recording attempt-only
            # metadata. Poisoned searches cannot starve later batches.
            try:
                async with session_factory() as db:
                    await search_execution.record_failed_attempt(db, sid)
                    await db.commit()
            except Exception as diagnostic:
                logger.error("saved-search attempt metadata unavailable: %s", type(diagnostic).__name__)
    return {"status": "ok" if not failed else "partial", "processed": processed,
            "matches": matches, "failed": failed}
