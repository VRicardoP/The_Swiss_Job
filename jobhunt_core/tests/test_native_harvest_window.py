"""A duplicate dispatch must not repeat an already completed public fetch."""

import asyncio
from contextlib import asynccontextmanager

import sqlalchemy as sa

from jobhunt_core.harvest.provider import BaseProvider
from jobhunt_core.harvest.types import FetchResult
from jobhunt_core.tasks import harvest
from jobhunt_core.tests.test_integration_runs import db, _seed_scopes, pytestmark


def test_same_window_only_fetches_once_and_disabled_scope_is_not_dispatched(
    db, monkeypatch
):
    factory, created = db
    enabled, disabled = _seed_scopes(factory, created, n=2)
    calls = []

    @asynccontextmanager
    async def sessions():
        yield factory

    class Provider(BaseProvider):
        name = "arbeitnow"

        async def fetch_new(self, params, cursor, http):
            calls.append(params)
            return FetchResult((), {})

    monkeypatch.setattr(harvest, "task_session_factory", sessions)
    monkeypatch.setattr(harvest, "get_provider", lambda _: Provider())

    async def check():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE harvest_scopes SET enabled=false WHERE id=:sid"),
                {"sid": disabled},
            )
            await s.commit()
        selected = await harvest._enabled_native_scope_ids()
        assert str(enabled) in selected and str(disabled) not in selected
        try:
            first = await harvest._run_scope_impl(
                str(enabled), run_key="native:window-1"
            )
            assert first.status == "ok"
            duplicate = await harvest._run_scope_impl(
                str(enabled), run_key="native:window-1"
            )
            assert duplicate.status == "skipped"
            assert len(calls) == 1
            next_window = await harvest._run_scope_impl(
                str(enabled), run_key="native:window-2"
            )
            assert next_window.status == "ok"
            assert len(calls) == 2
        finally:
            async with factory() as s:
                ids = (
                    (
                        await s.execute(
                            sa.text(
                                "SELECT run_id FROM source_harvest_runs WHERE scope_id=:sid"
                            ),
                            {"sid": enabled},
                        )
                    )
                    .scalars()
                    .all()
                )
                created["runs"].extend(ids)

    asyncio.run(check())
