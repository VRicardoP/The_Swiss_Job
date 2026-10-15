"""Adzuna survives the real fenced sink twice and exposes its aggregate."""

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
from jobhunt_core.tests.test_native_adzuna import row


def test_adzuna_double_cycle_preserves_raw_and_counts(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_adzuna as module

    monkeypatch.setattr(module, "PAGE_DELAY_SECONDS", 0)
    monkeypatch.setattr(module.settings, "CORE_ADZUNA_APP_ID", "client-id")
    monkeypatch.setattr(module.settings, "CORE_ADZUNA_APP_KEY", "client-secret")
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    raw = row()
    requests = []

    def transport(request):
        requests.append(request.url.path)
        return httpx.Response(
            200,
            json={"results": [raw] if request.url.path.endswith("/1") else []},
        )

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='adzuna' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.execute(
                sa.text(
                    "UPDATE harvest_scopes SET params=CAST(:params AS jsonb) "
                    "WHERE id=:id"
                ),
                {"id": scope_id, "params": '{"country":"us"}'},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            first = await run_scope(
                scope_id,
                get_provider("adzuna"),
                RawListingSink(),
                client,
                factory,
            )
            second = await run_scope(
                scope_id,
                get_provider("adzuna"),
                RawListingSink(),
                client,
                factory,
            )
        assert first.status == second.status == "ok"
        assert first.listings == second.listings == 1
        assert (
            requests
            == [
                "/v1/api/jobs/us/search/1",
                "/v1/api/jobs/us/search/2",
            ]
            * 2
        )

        async with factory() as session:
            stored = (
                await session.execute(
                    sa.text(
                        "SELECT r.raw, o.content FROM source_listing_revisions r "
                        "JOIN source_listing_incarnations i ON i.id=r.incarnation_id "
                        "JOIN vacancies v ON v.id=i.vacancy_id "
                        "JOIN offer_revisions o ON o.id=v.current_offer_revision_id "
                        "JOIN source_listings sl ON sl.id=i.source_listing_id "
                        "JOIN sources s ON s.id=sl.source_id "
                        "WHERE s.name='adzuna'"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "adzuna", item_limit=200, top_n=500, lease=60
            )

        assert len(stored) == 1
        assert stored[0].raw == {
            **raw,
            "_country": "us",
            "_core_category": "IT Jobs",
            "_core_order": 100,
        }
        assert stored[0].content["description"] == "Build React APIs with Docker."
        assert aggregate["total"] == 1
        assert aggregate["items"][0]["published_at"] == raw["created"]
        assert aggregate["counts"]["locations"] == {"United States": 1}
        assert aggregate["counts"]["categories"] == {"IT Jobs": 1}
        assert aggregate["counts"]["tags"] == {
            "developer": 1,
            "docker": 1,
            "python": 1,
            "r": 1,
            "react": 1,
        }

    asyncio.run(run())
