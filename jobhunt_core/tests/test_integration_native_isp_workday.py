"""Mosaic on Workday survives the real fenced sink twice."""

import asyncio
import json

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
from jobhunt_core.tests.test_native_isp_workday import EMPTY, body, posting


def test_isp_double_cycle_keeps_one_identity_per_offer(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_isp_workday as module

    monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    page = body([posting(1), posting(2)])

    def transport(request):
        offset = json.loads(request.content)["offset"]
        return httpx.Response(200, text=page if offset == 0 else EMPTY)

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='swiss_schools_isp' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            provider = get_provider("swiss_schools_isp")
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
                        "WHERE s.name='swiss_schools_isp' AND i.ended_at IS NULL "
                        "ORDER BY sl.external_id"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "swiss_schools_isp", item_limit=300, top_n=500, lease=60
            )

        assert [row.external_id for row in stored] == [
            "mosaic_geneva:JR213711",
            "mosaic_geneva:JR213712",
        ]
        assert stored[0].content["canton"] == "GE"
        assert aggregate["total"] == 2
        # Workday publishes a relative bucket, never a date.
        assert {item["published_at"] for item in aggregate["items"]} == {None}

    asyncio.run(run())
