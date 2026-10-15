"""jobs.admin.ch survives the real fenced sink twice, with everything the API gives."""

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
from jobhunt_core.tests.test_native_stelle_admin import body, row


def test_stelle_admin_double_cycle_keeps_one_identity_per_offer(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_stelle_admin as module

    monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    payload = body(
        [row(1), row(2, place="Genève, Suisse", home_office="true")], total=2
    )

    def transport(_request):
        return httpx.Response(200, text=payload)

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='stelle_admin' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            first = await run_scope(
                scope_id,
                get_provider("stelle_admin"),
                RawListingSink(),
                client,
                factory,
            )
            second = await run_scope(
                scope_id,
                get_provider("stelle_admin"),
                RawListingSink(),
                client,
                factory,
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
                        "WHERE s.name='stelle_admin' AND i.ended_at IS NULL "
                        "ORDER BY sl.external_id"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "stelle_admin", item_limit=300, top_n=500, lease=60
            )

        assert [r.external_id for r in stored] == ["id:10201991", "id:10201992"]
        # Everything the browser scraper left empty in all 43 production rows.
        assert stored[0].content["company"] == "VBS"
        assert stored[0].content["canton"] == "BE"
        assert stored[0].content["description"]
        assert stored[1].content["remote"] is True
        assert stored[1].content["canton"] == "GE"
        assert aggregate["total"] == 2
        assert {item["published_at"] for item in aggregate["items"]} == {
            "2026-09-24T08:09:27Z"
        }
        assert aggregate["counts"]["prof_groups"] == {"Fachfunktion": 2}

    asyncio.run(run())
