"""TES survives the real fenced sink twice with its advert date."""

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
from jobhunt_core.tests.test_native_tes import job, page


def test_tes_double_cycle_keeps_one_identity_per_offer(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_tes as module

    monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    body = page(
        [job(1), job(2, location="St. Gallen, Switzerland")],
        num_found=2,
        total_pages=1,
    )

    def transport(_request):
        return httpx.Response(200, text=body)

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='tes' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            first = await run_scope(
                scope_id, get_provider("tes"), RawListingSink(), client, factory
            )
            second = await run_scope(
                scope_id, get_provider("tes"), RawListingSink(), client, factory
            )
        assert first.status == second.status == "ok"
        assert first.listings == second.listings == 2

        async with factory() as session:
            stored = (
                await session.execute(
                    sa.text(
                        "SELECT sl.external_id, i.url, o.content "
                        "FROM source_listing_incarnations i "
                        "JOIN vacancies v ON v.id=i.vacancy_id "
                        "JOIN offer_revisions o ON o.id=v.current_offer_revision_id "
                        "JOIN source_listings sl ON sl.id=i.source_listing_id "
                        "JOIN sources s ON s.id=sl.source_id "
                        "WHERE s.name='tes' AND i.ended_at IS NULL "
                        "ORDER BY sl.external_id"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "tes", item_limit=300, top_n=500, lease=60
            )

        assert [row.external_id for row in stored] == ["id:2351631", "id:2351632"]
        assert stored[1].content["canton"] == "SG"
        assert aggregate["total"] == 2
        assert {item["published_at"] for item in aggregate["items"]} == {
            "2026-10-11T22:00:00.000Z"
        }

    asyncio.run(run())
