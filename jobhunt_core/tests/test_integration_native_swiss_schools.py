"""A watched board survives the sink twice, and an empty one stays healthy."""

import asyncio

import httpx
import sqlalchemy as sa

from jobhunt_core.harvest.providers import get_provider
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.source_aggregate import fetch_source_aggregate
from jobhunt_core.tests.test_integration_harvest import (  # noqa: F401
    _seed_scopes,
    db,
    pytestmark,
)
from jobhunt_core.tests.test_native_swiss_schools import ZIS, ZIS_EMPTY


def test_a_school_board_double_cycle_keeps_one_identity(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_swiss_schools as module

    monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)

    def transport(_request):
        return httpx.Response(200, text=ZIS)

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='swiss_schools_zis' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            provider = get_provider("swiss_schools_zis")
            first = await run_scope(
                scope_id, provider, RawListingSink(), client, factory
            )
            second = await run_scope(
                scope_id, provider, RawListingSink(), client, factory
            )
        assert first.status == second.status == "ok"
        assert first.listings == second.listings == 2

        async with factory() as session:
            stored = (
                await session.execute(
                    sa.text(
                        "SELECT sl.external_id, o.content FROM source_listing_incarnations i "
                        "JOIN vacancies v ON v.id=i.vacancy_id "
                        "JOIN offer_revisions o ON o.id=v.current_offer_revision_id "
                        "JOIN source_listings sl ON sl.id=i.source_listing_id "
                        "JOIN sources s ON s.id=sl.source_id "
                        "WHERE s.name='swiss_schools_zis' AND i.ended_at IS NULL "
                        "ORDER BY sl.external_id"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "swiss_schools_zis", item_limit=300, top_n=500, lease=60
            )

        assert [row.external_id for row in stored] == [
            "zis_zurich:4201",
            "zis_zurich:4202",
        ]
        assert stored[0].content["canton"] == "ZH"
        assert stored[0].content["tags"] == [
            "education",
            "international school",
            "zis_zurich",
        ]
        assert aggregate["total"] == 2
        # The boards publish no date: absent means unknown, never the ingest time.
        assert {item["published_at"] for item in aggregate["items"]} == {None}

    asyncio.run(run())


def test_an_empty_board_runs_ok_and_keeps_the_scope_healthy(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_swiss_schools as module

    monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='swiss_schools_zis' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, text=ZIS_EMPTY)
            )
        ) as client:
            provider = get_provider("swiss_schools_zis")
            results = [
                await run_scope(scope_id, provider, RawListingSink(), client, factory)
                for _ in range(3)
            ]
        assert [r.status for r in results] == ["ok", "ok", "ok"]

        async with factory() as session:
            state = (
                await session.execute(
                    sa.text(
                        "SELECT consecutive_failures, consecutive_blocks, "
                        "  last_complete_at IS NOT NULL AS completed "
                        "FROM source_scope_state WHERE scope_id=:sid"
                    ),
                    {"sid": scope_id},
                )
            ).one()

        # Three empty sweeps in a row leave a HEALTHY scope: no failures, no
        # blocks, and a confirmed complete harvest. That is the whole point of
        # the verified empty.
        assert state.consecutive_failures == 0
        assert state.consecutive_blocks == 0
        assert state.completed is True

    asyncio.run(run())
