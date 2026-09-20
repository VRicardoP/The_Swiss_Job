"""Per-scope six-hour work: bounded tasks, stable retries, no broker in tests."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from jobhunt_core.celery_app import celery_app
from jobhunt_core.harvest.types import ScopeRunResult
from jobhunt_core.tasks import harvest


def test_native_dispatch_has_explicit_core_route_and_cadence():
    entries = {row["task"]: row for row in celery_app.conf.beat_schedule.values()}
    assert "jobhunt.harvest.dispatch_native" in entries
    assert celery_app.conf.task_routes["jobhunt.harvest.dispatch_native"] == {
        "queue": "core.default"
    }

    cadence = entries["jobhunt.harvest.dispatch_native"]["schedule"]
    assert cadence.hour == {0, 6, 12, 18} and cadence.minute == {10}
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
        kwargs={"window": "2026-09-20T00:00:00+00:00"}
    )
    assert result.successful()
    assert result.result == {"window": "2026-09-20T00:00:00+00:00", "dispatched": 2}
    assert [call.kwargs for call in submit.call_args_list] == [
        {
            "kwargs": {
                "scope_id": sid,
                "run_key": f"native:2026-09-20T00:00:00+00:00:{sid}",
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
    result = harvest.dispatch_native_task.apply()
    # The second invocation crosses midnight but must not read a new window.
    clock.assert_called_once()
    assert result.successful()
    assert submit.call_count == 4
    assert {call.kwargs["kwargs"]["run_key"] for call in submit.call_args_list} == {
        "native:2026-09-20T18:00:00+00:00:one",
        "native:2026-09-20T18:00:00+00:00:two",
    }


def test_empty_dispatch_is_noop(monkeypatch):
    monkeypatch.setattr(
        harvest, "_enabled_native_scope_ids", AsyncMock(return_value=[])
    )
    submit = Mock()
    monkeypatch.setattr(harvest.run_scope_task, "apply_async", submit)
    result = harvest.dispatch_native_task.apply(
        kwargs={"window": "2026-09-20T00:00:00+00:00"}
    )
    assert result.successful() and result.result["dispatched"] == 0
    submit.assert_not_called()
