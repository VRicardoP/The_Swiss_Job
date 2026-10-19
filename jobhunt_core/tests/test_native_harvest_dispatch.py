"""Per-scope tiered work: bounded tasks, stable retries, no broker in tests."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from jobhunt_core.celery_app import celery_app
from jobhunt_core.harvest.types import ScopeRunResult
from jobhunt_core.tasks import harvest


def test_native_dispatch_has_explicit_core_route_and_cadence():
    entries = {
        name: row
        for name, row in celery_app.conf.beat_schedule.items()
        if row["task"] == "jobhunt.harvest.dispatch_native"
    }
    assert set(entries) == {
        "harvest-dispatch-native-6h",
        "harvest-dispatch-native-12h",
        "harvest-dispatch-native-24h",
    }
    assert celery_app.conf.task_routes["jobhunt.harvest.dispatch_native"] == {
        "queue": "core.default"
    }

    expected = {
        "harvest-dispatch-native-6h": ({1, 7, 13, 19}, 6),
        "harvest-dispatch-native-12h": ({9, 21}, 12),
        "harvest-dispatch-native-24h": ({23}, 24),
    }
    for name, (hours, cadence_hours) in expected.items():
        cadence = entries[name]["schedule"]
        assert cadence.hour == hours and cadence.minute == {30}
        assert entries[name]["kwargs"] == {"cadence_hours": cadence_hours}
    for task, expected in [
        ("jobhunt.harvest.dispatch_native", "core.default"),
        ("jobhunt.harvest.run_scope", "core.harvest"),
    ]:
        routed = celery_app.amqp.router.route({}, task, args=(), kwargs={})
        assert routed["queue"].name == expected


def test_dispatch_sends_independent_scope_tasks_with_stable_keys(monkeypatch):
    monkeypatch.setattr(
        harvest, "_enabled_native_scope_ids", AsyncMock(return_value=["one", "two"])
    )
    submit = Mock()
    monkeypatch.setattr(harvest.run_scope_task, "apply_async", submit)
    result = harvest.dispatch_native_task.apply(
        kwargs={"cadence_hours": 6, "window": "2026-09-20T00:00:00+00:00"}
    )
    assert result.successful()
    assert result.result == {
        "cadence_hours": 6,
        "window": "2026-09-20T00:00:00+00:00",
        "dispatched": 2,
    }
    harvest._enabled_native_scope_ids.assert_awaited_once_with(6)
    assert [call.kwargs for call in submit.call_args_list] == [
        {
            "kwargs": {
                "scope_id": sid,
                "run_key": f"native:6h:2026-09-20T00:00:00+00:00:{sid}",
            },
            "expires": 21600,
        }
        for sid in ["one", "two"]
    ]


def test_native_worker_threads_the_stable_key_to_claim(monkeypatch):
    impl = AsyncMock(return_value=ScopeRunResult(scope_id="one", status="ok"))
    monkeypatch.setattr(harvest, "_run_scope_impl", impl)
    result = harvest.run_scope_task.apply(
        kwargs={"scope_id": "one", "run_key": "native:2026-09-20T00:00:00+00:00:one"}
    )
    assert result.successful()
    impl.assert_awaited_once_with("one", run_key="native:2026-09-20T00:00:00+00:00:one")


def test_dispatch_retry_preserves_window_across_midnight(monkeypatch):
    monkeypatch.setattr(
        harvest, "_enabled_native_scope_ids", AsyncMock(return_value=["one", "two"])
    )
    submit = Mock(side_effect=[None, ConnectionError("broker down"), None, None])
    monkeypatch.setattr(harvest.run_scope_task, "apply_async", submit)
    clock = Mock(
        side_effect=[
            datetime(2026, 9, 20, 23, 59, tzinfo=timezone.utc),
            datetime(2026, 9, 21, 0, 1, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(harvest, "datetime", SimpleNamespace(now=clock))
    result = harvest.dispatch_native_task.apply(kwargs={"cadence_hours": 6})
    # The second invocation crosses midnight but must not read a new window.
    clock.assert_called_once()
    assert result.successful()
    assert submit.call_count == 4
    assert {call.kwargs["kwargs"]["run_key"] for call in submit.call_args_list} == {
        "native:6h:2026-09-20T18:00:00+00:00:one",
        "native:6h:2026-09-20T18:00:00+00:00:two",
    }


def test_empty_dispatch_is_noop(monkeypatch):
    monkeypatch.setattr(
        harvest, "_enabled_native_scope_ids", AsyncMock(return_value=[])
    )
    submit = Mock()
    monkeypatch.setattr(harvest.run_scope_task, "apply_async", submit)
    result = harvest.dispatch_native_task.apply(
        kwargs={"cadence_hours": 24, "window": "2026-09-20T00:00:00+00:00"}
    )
    assert result.successful() and result.result["dispatched"] == 0
    submit.assert_not_called()


def test_source_cadence_is_explicit_and_new_sources_default_to_daily():
    assert all(
        harvest.source_cadence_hours(source) == 6 for source in harvest.SIX_HOUR_SOURCES
    )
    assert all(
        harvest.source_cadence_hours(source) == 12
        for source in harvest.TWELVE_HOUR_SOURCES
    )
    assert harvest.source_cadence_hours("future_provider") == 24
    assert harvest.source_cadence_hours("JSearch") is None


def test_failure_backoff_waits_12_24_then_48_hours():
    assert [harvest.failure_backoff_hours(n) for n in range(6)] == [
        0,
        0,
        12,
        24,
        48,
        48,
    ]
    assert harvest.backoff_elapsed(2, 12 * 3600)
    assert not harvest.backoff_elapsed(2, 12 * 3600 - 1)
    assert harvest.backoff_elapsed(4, 48 * 3600)
    assert not harvest.backoff_elapsed(4, 48 * 3600 - 1)
    assert harvest.backoff_elapsed(4, None)
