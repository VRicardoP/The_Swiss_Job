"""Real task boundary and fair per-search failure isolation, no delivery I/O."""

import asyncio
from unittest.mock import AsyncMock

import sqlalchemy as sa

from jobhunt_core.config import settings
from jobhunt_core.tasks import searches
from jobhunt_core.tests.test_integration_search_execution import (
    db, pytestmark, corpus, new_search, state,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
)


def test_disabled_tasks_never_open_database(monkeypatch):
    monkeypatch.setattr(settings, "CORE_SAVED_SEARCH_EXECUTION_ENABLED", False)

    def forbidden():
        raise AssertionError("disabled task attempted DB access")

    monkeypatch.setattr(searches, "task_session_factory", forbidden)
    expected = {"status": "disabled", "processed": 0, "matches": 0, "failed": 0}
    assert searches.run_due_task.apply().get() == expected
    assert searches.run_one_task.apply(args=["unused-id"]).get() == expected


def test_task_executes_coroutine_instead_of_returning_it(monkeypatch):
    monkeypatch.setattr(settings, "CORE_SAVED_SEARCH_EXECUTION_ENABLED", True)
    outcome = {"status": "ok", "processed": 2, "matches": 3, "failed": 0}
    execute = AsyncMock(return_value=outcome)
    monkeypatch.setattr(searches, "_run", execute)
    assert searches.run_due_task.apply(kwargs={"limit": 7}).get() == outcome
    execute.assert_awaited_once_with(limit=7)


def test_failed_search_does_not_starve_healthy_search_or_consume_observations(db, monkeypatch):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    corpus(factory, made, "Python developer")
    monkeypatch.setattr(settings, "CORE_SAVED_SEARCH_EXECUTION_ENABLED", True)
    real_execute = searches.search_execution.execute_search

    async def go():
        async with factory() as s:
            bad, _, bad_tenant, _ = await new_search(s, made, name="bad")
            good, _, good_tenant, _ = await new_search(s, made, name="good")
            monkeypatch.setattr(settings, "CORE_DELIVERY_HTTP_DESTINATIONS", {
                bad_tenant: "http://never-called.invalid", good_tenant: "http://never-called.invalid",
            })
            async def poison(session, sid, **kwargs):
                result = await real_execute(session, sid, **kwargs)
                if sid == bad:
                    raise ValueError("synthetic failure after writing")
                return result
            monkeypatch.setattr(searches.search_execution, "execute_search", poison)
            result = await searches._run(session_factory=factory)
            assert result == {"status": "partial", "processed": 1, "matches": 1, "failed": 1}
            st = await state(s, bad)
            assert st.last_run_at is None and (st.total_matches, st.observed, st.events) == (0, 0, 0)
            assert (await state(s, good)).events == 1
            assert await s.scalar(sa.text("SELECT last_attempt_at FROM saved_search_execution WHERE saved_search_id=:id"), {"id": bad}) is not None
            # A newly configured search precedes the failed one in bounded sweeps.
            fresh, _, _, _ = await new_search(s, made, name="fresh")
            assert await searches.search_execution.due_search_ids(s, limit=1) == [fresh]
    asyncio.run(go())
