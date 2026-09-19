"""A slow source cannot occupy the first position after every interrupted sweep."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from config import settings
from models.source_health import SourceHealth
from services import source_health
from tasks import fetch_tasks, scraping_tasks


async def test_order_is_oldest_attempt_first_without_changing_health(db_session):
    now = datetime.now(timezone.utc)
    old = source_health._new_row("old")
    old.last_attempt_at = now - timedelta(days=10)
    old.last_outcome = "error"
    recent = source_health._new_row("recent")
    recent.last_attempt_at = now
    db_session.add_all([old, recent])
    await db_session.commit()
    assert await source_health.oldest_attempt_first(
        db_session, ["recent", "old", "new"]
    ) == ["new", "old", "recent"]
    assert old.last_outcome == "error"


async def test_start_is_not_success_and_preserves_failure_history(db_session):
    old = source_health._new_row("source")
    previous = datetime.now(timezone.utc) - timedelta(days=10)
    old.last_attempt_at = previous
    old.last_success_at = previous
    old.last_outcome = "ok"
    old.last_stored_count = 7
    old.consecutive_errors = 3
    old.consecutive_unstored = 2
    db_session.add(old)
    await db_session.commit()
    await source_health.record_attempt(db_session, "source")
    await db_session.rollback()
    db_session.expire_all()  # inspect the committed UPSERT, not the pre-write ORM snapshot
    row = await db_session.scalar(
        select(SourceHealth).where(SourceHealth.source_key == "source")
    )
    assert row.last_attempt_at > previous
    assert row.last_success_at == previous and row.last_outcome is None
    assert row.last_stored_count == 7
    assert row.consecutive_errors == 3 and row.consecutive_unstored == 2


async def test_interrupted_scraper_rotates_and_does_not_claim_success(
    db_session, monkeypatch
):
    monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", False)
    calls = []

    async def slow_fetch(*args):
        calls.append("aaa_slow")
        raise SoftTimeLimitExceeded()

    async def school_fetch(*args):
        calls.append("school")
        return []

    def scraper(name, fetch):
        return SimpleNamespace(
            get_source_name=lambda: name, fetch_jobs=fetch, WATCHLIST_SOURCE=False
        )

    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr(scraping_tasks, "task_session", session)
    monkeypatch.setattr(
        scraping_tasks,
        "get_all_scrapers",
        lambda: [
            scraper("aaa_slow", slow_fetch),
            scraper("school", school_fetch),
        ],
    )
    first = await scraping_tasks._fetch_scrapers_async()
    assert first["soft_time_limit"] and calls == ["aaa_slow"]
    await db_session.rollback()  # interruption does not erase the attempt
    row = await db_session.scalar(
        select(SourceHealth).where(SourceHealth.source_key == "aaa_slow")
    )
    assert row is not None and row.last_attempt_at is not None
    assert row.last_success_at is None and row.last_outcome is None
    calls.clear()
    second = await scraping_tasks._fetch_scrapers_async()
    assert calls == ["school", "aaa_slow"]
    assert second["scrapers"] == 1 and second["soft_time_limit"]


async def test_provider_persistence_rotates_before_budget_exhaustion(
    db_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    for name, age in (("recent", 0), ("old", 10)):
        row = source_health._new_row(name)
        row.last_attempt_at = now - timedelta(days=age)
        db_session.add(row)
    await db_session.commit()
    providers = [
        SimpleNamespace(
            get_source_name=lambda n=n: n, fetch_jobs=AsyncMock(return_value=[])
        )
        for n in ("recent", "old")
    ]

    @asynccontextmanager
    async def session():
        yield db_session

    recorded = []
    original = source_health.record_and_alert

    async def record(db, name, *args):
        recorded.append(name)
        return await original(db, name, *args)

    monkeypatch.setattr(fetch_tasks, "task_session", session)
    monkeypatch.setattr(fetch_tasks, "get_all_providers", lambda: providers)
    monkeypatch.setattr(source_health, "record_and_alert", record)
    await fetch_tasks._fetch_providers_async()
    assert recorded == ["old", "recent"]
