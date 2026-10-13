"""Native runtime seams extracted before deleting the historical shadow engine."""

import asyncio
from contextlib import asynccontextmanager
import logging
import uuid

from jobhunt_core.tasks import delivery as delivery_tasks
from jobhunt_core.tasks import matching as matching_tasks


def test_matching_recovery_is_bounded_and_expires_with_its_tick():
    from jobhunt_core.celery_app import celery_app
    from jobhunt_core.config import settings

    assert matching_tasks.RECOVERY_EMBEDDING_ROUNDS == 1
    assert matching_tasks.RECOVERY_MAX_PROFILES == 1
    assert celery_app.conf.task_routes["jobhunt.matching.recover"] == {
        "queue": "core.matching"
    }
    entry = celery_app.conf.beat_schedule["matching-recovery"]
    cadence = float(settings.CORE_MATCHING_RECOVERY_EVERY_S)
    assert entry == {
        "task": "jobhunt.matching.recover",
        "schedule": cadence,
        "options": {"expires": cadence},
    }


def test_matching_recovery_has_no_legacy_consumer_dependency():
    sql = matching_tasks._RECOVERY_NEEDED_SQL
    assert "p.projection_active" in sql
    assert "ORDER BY last_try ASC NULLS FIRST, ca.id" in sql
    for legacy_name in ("shadow_change_log", "inactive_user_refs", "swissjob-shadow"):
        assert legacy_name not in sql


def test_embedding_drain_never_exceeds_one_round(monkeypatch):
    calls = []

    async def still_busy(limit, session_factory=None):
        calls.append((limit, session_factory))
        return {"embedded": {"m": 1}, "profiles_embedded": {"m": 1}}

    monkeypatch.setattr(matching_tasks, "_run_pending_impl", still_busy)
    factory = object()
    asyncio.run(
        matching_tasks._drain_embeddings(factory, limit=17, max_rounds=50)
    )
    assert calls == [(17, factory)]


def test_delivery_health_reports_green_and_alerts(monkeypatch, caplog):
    @asynccontextmanager
    async def factory():
        yield object()

    async def green(_session):
        return {"by_state": {}, "oldest_pending_s": 900.0, "dead_total": 0}

    monkeypatch.setattr(delivery_tasks.delivery, "stats", green)
    assert asyncio.run(delivery_tasks._check_health_impl(factory)) == {
        "by_state": {},
        "oldest_pending_s": 900.0,
        "dead_total": 0,
        "healthy": True,
        "alerts": [],
    }

    async def red(_session):
        return {
            "by_state": {"dead": 1},
            "oldest_pending_s": 900.1,
            "dead_total": 1,
        }

    monkeypatch.setattr(delivery_tasks.delivery, "stats", red)
    with caplog.at_level(logging.ERROR, logger=delivery_tasks.__name__):
        result = asyncio.run(delivery_tasks._check_health_impl(factory))
    assert result["healthy"] is False
    assert result["alerts"] == ["oldest_pending_s", "dead_total"]
    assert "delivery_health" in caplog.text


def test_recovery_selects_at_most_one_target(monkeypatch):
    first, second = uuid.uuid4(), uuid.uuid4()

    class Result:
        def all(self):
            return [type("Row", (), {"id": first})()]

    class Session:
        async def execute(self, _statement, params):
            assert params["cap"] == 1
            return Result()

    targets = asyncio.run(
        matching_tasks._recovery_targets(Session(), {second}, limit=20)
    )
    assert targets == [first]
