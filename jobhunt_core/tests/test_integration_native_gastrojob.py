"""Gastrojob survives the real fenced sink twice with its Swiss date."""

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
from jobhunt_core.tests.test_native_gastrojob import ad, detail, fragment


def test_gastrojob_double_cycle_keeps_one_identity_per_offer(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_gastrojob as module

    monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    # Twenty announced offers: two pages, and the second repeats one of the
    # first, which the portal does.
    first = fragment(ad(1) + ad(2), count=20)
    second = fragment(ad(2) + ad(3), count=20)
    requests = []

    def transport(request):
        url = str(request.url)
        requests.append(url)
        if "tx_mxngastrojob_ajax" in url:
            page = int(
                request.url.params["tx_mxngastrojob_ajax[@widget_0][currentPage]"]
            )
            return httpx.Response(
                200, text={1: first, 2: second}.get(page, fragment(count=20))
            )
        return httpx.Response(200, text=detail())

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='gastrojob' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            first_run = await run_scope(
                scope_id, get_provider("gastrojob"), RawListingSink(), client, factory
            )
            second_run = await run_scope(
                scope_id, get_provider("gastrojob"), RawListingSink(), client, factory
            )
        assert first_run.status == second_run.status == "ok"
        assert first_run.listings == second_run.listings == 3

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
                        "WHERE s.name='gastrojob' AND i.ended_at IS NULL "
                        "ORDER BY sl.external_id"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "gastrojob", item_limit=300, top_n=500, lease=60
            )

        assert [row.external_id for row in stored] == [
            "id:56181",
            "id:56182",
            "id:56183",
        ]
        assert stored[0].url == "https://www.gastrojob.ch/stellen/stelleninserat/56181"
        assert stored[0].content["canton"] == "BE"
        assert stored[0].content["employment_type"] == "Vollzeit"
        assert aggregate["total"] == 3
        assert {item["published_at"] for item in aggregate["items"]} == {
            "2026-10-14T07:53:00+02:00"
        }
        assert aggregate["counts"]["locations"] == {"Bern, Bern": 3}

    asyncio.run(run())
