"""The sealed export uses the executor query and never consumes an alert."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from config import settings
from models.job import Job
from models.jobhunt_profile_map import JobhuntProfileMap
from models.notification import Notification
from services.search_handover import SearchCaptureError, capture
from tasks.search_tasks import candidate_query
from tests.test_g2_fix_saved_searches import _make_user, _make_job, _make_search


async def fixture(db, monkeypatch):
    monkeypatch.setattr(settings, "SAVED_SEARCH_WRITES_FROZEN", True)
    uid = await _make_user(db)
    db.add(JobhuntProfileMap(user_id=uid, core_profile_id=uuid.uuid4()))
    await db.commit()
    search = await _make_search(db, uid, "ZH", uuid.uuid4().hex[:8])
    stamp = datetime.now(timezone.utc)
    for name, delta, canton in (
        ("sent", -1, "ZH"),
        ("owed", -1, "ZH"),
        ("future", 1, "ZH"),
        ("elsewhere", -1, "BE"),
    ):
        await _make_job(
            db, name, canton=canton, first_seen_at=stamp + timedelta(hours=delta)
        )
    return search, stamp


async def test_capture_preserves_pending_and_sent_without_mutation(
    db_session, monkeypatch
):
    search, stamp = await fixture(db_session, monkeypatch)
    previous = (search.last_run_at, search.total_matches)
    redis = AsyncMock()
    redis.mget.return_value = [None, b"1"]  # ordered hashes: owed, sent
    snapshot = await capture(db_session, redis, settings, captured_at=stamp)
    sid = str(search.id)
    executor_hashes = set(
        (await db_session.execute(candidate_query(search, settings, stamp))).scalars()
    )
    assert (
        executor_hashes
        == {row["hash"] for row in snapshot["candidates"][sid]}
        == {"owed", "sent"}
    )
    assert snapshot["sent"][sid] == {"owed": False, "sent": True}
    assert (search.last_run_at, search.total_matches) == previous
    assert not (await db_session.execute(select(Notification))).scalars().all()
    assert [call[0] for call in redis.method_calls] == ["mget"]
    assert snapshot["jobs_fingerprint"]["n"] == 4


async def test_capture_refuses_unfrozen_and_unbound_owners(db_session, monkeypatch):
    monkeypatch.setattr(settings, "SAVED_SEARCH_WRITES_FROZEN", False)
    with pytest.raises(SearchCaptureError, match="frozen"):
        await capture(
            db_session, AsyncMock(), settings, captured_at=datetime.now(timezone.utc)
        )
    monkeypatch.setattr(settings, "SAVED_SEARCH_WRITES_FROZEN", True)
    uid = await _make_user(db_session)
    await _make_search(db_session, uid, "ZH", "unbound")
    with pytest.raises(SearchCaptureError, match="binding"):
        await capture(
            db_session, AsyncMock(), settings, captured_at=datetime.now(timezone.utc)
        )


@pytest.mark.parametrize(
    "response", [[], [None, b"wrong"], ConnectionError("redis unavailable")]
)
async def test_marker_errors_abort_capture(db_session, monkeypatch, response):
    _, stamp = await fixture(db_session, monkeypatch)
    redis = AsyncMock()
    if isinstance(response, Exception):
        redis.mget.side_effect = response
        error = ConnectionError
    else:
        redis.mget.return_value = response
        error = SearchCaptureError
    with pytest.raises(error):
        await capture(db_session, redis, settings, captured_at=stamp)
    assert [call[0] for call in redis.method_calls] == ["mget"]


async def test_capture_detects_corpus_changes_not_just_new_rows(
    db_session, monkeypatch
):
    _, stamp = await fixture(db_session, monkeypatch)
    redis = AsyncMock()
    redis.mget.side_effect = lambda keys: [None] * len(keys)
    before = await capture(db_session, redis, settings, captured_at=stamp)
    job = await db_session.get(Job, "elsewhere")
    job.canton = "ZH"
    await db_session.flush()
    after = await capture(db_session, redis, settings, captured_at=stamp)
    assert before["jobs_fingerprint"]["n"] == after["jobs_fingerprint"]["n"]
    assert before["jobs_fingerprint"]["digest"] != after["jobs_fingerprint"]["digest"]


async def test_health_searches_exposes_the_actual_freeze(client, monkeypatch):
    for value, expected in ((False, "enabled"), (True, "frozen")):
        monkeypatch.setattr(settings, "SAVED_SEARCH_WRITES_FROZEN", value)
        response = await client.get("/health/searches")
        assert response.status_code == 200
        assert response.json() == {"writes": expected}


async def test_capture_timestamp_follows_the_writer_drain(db_session, monkeypatch):
    import asyncio
    from sqlalchemy import text
    from tests.conftest import TestSessionLocal

    await fixture(db_session, monkeypatch)
    await db_session.execute(text("LOCK TABLE jobs IN ROW EXCLUSIVE MODE"))
    waiting = asyncio.Event()
    markers = AsyncMock()
    markers.mget.side_effect = lambda keys: [None] * len(keys)

    async def reader():
        async with TestSessionLocal() as db:
            execute = db.execute

            async def observe(statement, *args, **kwargs):
                if str(statement).startswith("LOCK TABLE"):
                    waiting.set()
                return await execute(statement, *args, **kwargs)

            monkeypatch.setattr(db, "execute", observe)
            return await capture(db, markers, settings)

    task = asyncio.create_task(reader())
    try:
        await asyncio.wait_for(waiting.wait(), 3)
        await asyncio.sleep(0.1)
        assert not task.done()
        boundary = await db_session.scalar(text("SELECT clock_timestamp()"))
        await db_session.commit()
        result = await asyncio.wait_for(task, 5)
        assert result["captured_at"] >= boundary
    finally:
        await db_session.rollback()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
