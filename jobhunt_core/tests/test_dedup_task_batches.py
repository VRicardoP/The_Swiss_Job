"""Bounded orchestration for the daily semantic dedup scan."""

from unittest.mock import patch

from jobhunt_core.tasks import maintenance


def test_partial_fragment_enqueues_exact_frozen_continuation(monkeypatch):
    async def fragment(*args):
        assert args == (None, None, None, None)
        return {
            "status": "partial",
            "complete": False,
            "next_cursor": {
                "created_at": "2026-10-11T03:00:00+00:00",
                "vacancy_id": "11111111-1111-1111-1111-111111111111",
            },
            "window_start": "2026-10-09T04:00:00+00:00",
            "window_end": "2026-10-11T04:00:00+00:00",
        }

    monkeypatch.setattr(maintenance, "_run_dedup_fragment", fragment)
    with patch.object(maintenance.dedup_scan_task, "apply_async") as enqueue:
        result = maintenance.dedup_scan_task.run()

    assert result["continuation_enqueued"] is True
    enqueue.assert_called_once_with(
        kwargs={
            "window_start": "2026-10-09T04:00:00+00:00",
            "window_end": "2026-10-11T04:00:00+00:00",
            "after_created_at": "2026-10-11T03:00:00+00:00",
            "after_id": "11111111-1111-1111-1111-111111111111",
        },
        countdown=1,
    )


def test_final_fragment_does_not_enqueue(monkeypatch):
    async def fragment(*args):
        return {
            "status": "ok",
            "complete": True,
            "next_cursor": None,
            "window_start": "2026-10-09T04:00:00+00:00",
            "window_end": "2026-10-11T04:00:00+00:00",
        }

    monkeypatch.setattr(maintenance, "_run_dedup_fragment", fragment)
    with patch.object(maintenance.dedup_scan_task, "apply_async") as enqueue:
        result = maintenance.dedup_scan_task.run()

    assert "continuation_enqueued" not in result
    enqueue.assert_not_called()
