"""JSearch survives the real fenced sink twice and exposes its dimensions."""

import asyncio

import httpx
import sqlalchemy as sa

from jobhunt_core.harvest.providers import get_provider
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.provision_jsearch import SCOPE_PARAMS
from jobhunt_core.source_aggregate import fetch_source_aggregate
from jobhunt_core.tests.test_integration_harvest import (  # noqa: F401
    _seed_scopes,
    db,
    pytestmark,
)
from jobhunt_core.tests.test_native_jsearch import row


def test_jsearch_double_cycle_preserves_raw_and_dimensions(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_jsearch as module

    monkeypatch.setattr(module.settings, "CORE_JSEARCH_RAPIDAPI_KEY", "rapid-secret")
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    raw = row()
    requests = []

    def transport(request):
        requests.append(dict(request.url.params))
        return httpx.Response(200, json={"data": [raw]})

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='jsearch' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.execute(
                sa.text(
                    "UPDATE harvest_scopes SET params=CAST(:params AS jsonb) WHERE id=:id"
                ),
                {"id": scope_id, "params": __import__("json").dumps(SCOPE_PARAMS[0])},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            first = await run_scope(
                scope_id, get_provider("jsearch"), RawListingSink(), client, factory
            )
            second = await run_scope(
                scope_id, get_provider("jsearch"), RawListingSink(), client, factory
            )
        assert first.status == second.status == "ok"
        assert first.listings == second.listings == 1
        assert len(requests) == 2

        async with factory() as session:
            stored = (
                await session.execute(
                    sa.text(
                        "SELECT r.raw,o.content FROM source_listing_revisions r "
                        "JOIN source_listing_incarnations i ON i.id=r.incarnation_id "
                        "JOIN vacancies v ON v.id=i.vacancy_id "
                        "JOIN offer_revisions o ON o.id=v.current_offer_revision_id "
                        "JOIN source_listings sl ON sl.id=i.source_listing_id "
                        "JOIN sources s ON s.id=sl.source_id WHERE s.name='jsearch'"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "jsearch", item_limit=200, top_n=500, lease=60
            )

        assert len(stored) == 1
        assert stored[0].raw == {**raw, "_core_order": 0}
        assert aggregate["counts"]["locations"] == {"New York, NY, US": 1}
        assert aggregate["counts"]["companies"] == {"Acme Corp": 1}
        assert aggregate["counts"]["publishers"] == {"LinkedIn": 1}
        assert aggregate["counts"]["job_types"] == {"FULLTIME": 1}
        assert aggregate["last_query"] == "software developer"
        item = aggregate["items"][0]
        assert item["provider_id"] == raw["job_id"]
        assert item["city"] == "New York"
        assert item["state"] == "NY"
        assert item["country"] == "US"
        assert item["publisher"] == "LinkedIn"
        assert item["published_at"] == raw["job_posted_at_datetime_utc"]

    asyncio.run(run())
