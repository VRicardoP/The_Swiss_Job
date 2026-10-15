"""schuljobs survives the real fenced sink twice with its JSON-LD date."""

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
from jobhunt_core.tests.test_native_schuljobs import card, detail, listing, scroll


def test_schuljobs_double_cycle_keeps_one_identity_per_offer(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_schuljobs as module

    monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    initial = listing(card(1) + card(2))

    def transport(request):
        url = str(request.url)
        if url.endswith("/suche"):
            return httpx.Response(200, text=initial)
        if "/scroll/" in url:
            return httpx.Response(200, text=scroll())
        return httpx.Response(200, text=detail(date="2026-10-14"))

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='schuljobs' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.execute(
                sa.text(
                    "UPDATE harvest_scopes SET params='{\"max_pages\": 4}'::jsonb "
                    "WHERE id=:id"
                ),
                {"id": scope_id},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            provider = get_provider("schuljobs")
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
                        "WHERE s.name='schuljobs' AND i.ended_at IS NULL "
                        "ORDER BY sl.external_id"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "schuljobs", item_limit=300, top_n=500, lease=60
            )

        assert [row.external_id for row in stored] == ["id:J991991", "id:J991992"]
        assert stored[0].content["canton"] == "ZH"
        assert stored[0].content["description"] == "Unterrichten"
        assert aggregate["total"] == 2
        assert {item["published_at"] for item in aggregate["items"]} == {"2026-10-14"}

    asyncio.run(run())
