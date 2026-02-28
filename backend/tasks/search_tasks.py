"""Celery tasks: execute saved searches and dispatch notifications."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.search_tasks.run_saved_searches",
    bind=True,
    max_retries=1,
)
def run_saved_searches(self) -> dict[str, Any]:
    """Run all active saved searches whose schedule is due."""
    try:
        return asyncio.run(_run_saved_searches_async())
    except Exception as exc:
        logger.error("run_saved_searches failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _run_saved_searches_async() -> dict[str, Any]:
    """Async implementation: find due searches, run matching, create notifications."""
    from sqlalchemy import select

    from config import settings
    from database import task_session
    from models.enums import NotifyFrequency
    from models.saved_search import SavedSearch

    now = datetime.now(timezone.utc)
    processed = 0
    total_matches = 0

    async with task_session() as db:
        # Find active searches that are due
        stmt = select(SavedSearch).where(
            SavedSearch.is_active.is_(True),
        )
        result = await db.execute(stmt)
        searches = result.scalars().all()

        for search in searches:
            # Check if search is due based on frequency
            if search.last_run_at:
                if search.notify_frequency == NotifyFrequency.realtime:
                    interval = timedelta(minutes=5)
                elif search.notify_frequency == NotifyFrequency.daily:
                    interval = timedelta(hours=24)
                elif search.notify_frequency == NotifyFrequency.weekly:
                    interval = timedelta(weeks=1)
                else:
                    continue

                if now - search.last_run_at < interval:
                    continue

            matches = await _execute_single_search(db, search, settings)
            total_matches += matches
            processed += 1

    return {
        "status": "success",
        "searches_processed": processed,
        "total_matches": total_matches,
    }


async def _execute_single_search(db, search, settings) -> int:
    """Execute a single saved search and create notifications if matches found."""
    from datetime import datetime, timezone

    from models.notification import Notification
    from models.job import Job
    from sqlalchemy import select, func

    now = datetime.now(timezone.utc)
    filters = search.filters or {}
    min_score = max(search.min_score, settings.ALERTS_MIN_SCORE_THRESHOLD)

    # Build query from filters
    conditions = [Job.is_active.is_(True), Job.duplicate_of.is_(None)]

    if filters.get("source"):
        sources = [s.strip() for s in filters["source"].split(",") if s.strip()]
        if sources:
            conditions.append(Job.source.in_(sources))

    if filters.get("canton"):
        cantons = [c.strip().upper() for c in filters["canton"].split(",") if c.strip()]
        if cantons:
            conditions.append(Job.canton.in_(cantons))

    if filters.get("remote_only"):
        conditions.append(Job.remote.is_(True))

    if filters.get("language"):
        conditions.append(Job.language == filters["language"])

    # Only jobs seen since last run
    if search.last_run_at:
        conditions.append(Job.last_seen_at >= search.last_run_at)

    stmt = select(func.count()).select_from(Job).where(*conditions)
    match_count = (await db.execute(stmt)).scalar_one()

    # Update search metadata
    search.last_run_at = now
    search.total_matches = (search.total_matches or 0) + match_count

    if match_count > 0 and match_count >= min_score:
        # Create notification
        notification = Notification(
            user_id=search.user_id,
            event_type="new_matches",
            title=f"New matches for '{search.name}'",
            body=f"Found {match_count} new jobs matching your saved search.",
            data={
                "search_id": str(search.id),
                "search_name": search.name,
                "match_count": match_count,
            },
        )
        db.add(notification)

        # Broadcast SSE (fire-and-forget via Redis)
        if search.notify_push:
            try:
                import redis

                from config import settings as cfg

                r = redis.from_url(cfg.REDIS_URL)
                import json

                r.publish(
                    f"sse:{search.user_id}",
                    json.dumps(
                        {
                            "event": "new_matches",
                            "data": {
                                "search_id": str(search.id),
                                "search_name": search.name,
                                "match_count": match_count,
                                "notification_id": str(notification.id),
                            },
                        }
                    ),
                )
                r.close()
            except Exception:
                logger.warning("Failed to broadcast SSE for search %s", search.id)

    await db.commit()
    return match_count


@celery_app.task(
    name="tasks.search_tasks.run_single_saved_search",
    bind=True,
    max_retries=1,
)
def run_single_saved_search(
    self, search_id: str, user_id: str
) -> dict[str, Any]:
    """Run a single saved search manually (triggered from API)."""
    try:
        return asyncio.run(_run_single_async(search_id, user_id))
    except Exception as exc:
        logger.error("run_single_saved_search failed for %s: %s", search_id, exc)
        raise self.retry(exc=exc, countdown=30)


async def _run_single_async(search_id: str, user_id: str) -> dict[str, Any]:
    """Async implementation for manual single search run."""
    import uuid as uuid_mod

    from sqlalchemy import select

    from config import settings
    from database import task_session
    from models.saved_search import SavedSearch

    uid = uuid_mod.UUID(search_id)

    async with task_session() as db:
        search = (
            await db.execute(
                select(SavedSearch).where(
                    SavedSearch.id == uid,
                    SavedSearch.user_id == uuid_mod.UUID(user_id),
                )
            )
        ).scalar_one_or_none()

        if search is None:
            return {"status": "error", "reason": "search_not_found"}

        matches = await _execute_single_search(db, search, settings)
        return {
            "status": "success",
            "search_id": search_id,
            "matches": matches,
        }
