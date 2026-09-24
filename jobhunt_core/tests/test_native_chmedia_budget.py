"""A stream timeout must bound wall time, not just idle time between chunks."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

from jobhunt_core.tests.test_native_chmedia import fetch, raw


def test_each_request_has_absolute_timeout_and_late_timeout_preserves_prefix(
    monkeypatch,
):
    from jobhunt_core.harvest.providers import native_chmedia as module

    budgets = []

    @asynccontextmanager
    async def timeout(seconds):
        budgets.append(seconds)
        if len(budgets) == 2:
            raise TimeoutError()
        yield

    monkeypatch.setattr(module.asyncio, "timeout", timeout)
    result, requests = fetch(
        pages=[{"items": [raw()]}, {"items": [raw(2)]}], monkeypatch=monkeypatch
    )
    assert len(budgets) == 2 and all(0 < n <= 25 for n in budgets)
    assert len(requests) == 1 and len(result.listings) == 1
    assert not result.complete and result.error == "TimeoutError"


def test_elapsed_budget_stops_before_next_request(monkeypatch):
    from jobhunt_core.harvest.providers import native_chmedia as module

    ticks = iter((0, 0, 601))
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    result, requests = fetch(
        pages=[{"items": [raw()]}, {"items": [raw(2)]}], monkeypatch=monkeypatch
    )
    assert len(requests) == 1 and len(result.listings) == 1
    assert not result.complete and result.error is None
