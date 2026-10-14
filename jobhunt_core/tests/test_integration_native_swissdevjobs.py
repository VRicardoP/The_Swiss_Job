"""SwissDevJobs cursor enrichment survives the real fenced sink twice."""

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
from jobhunt_core.tests.test_native_swissdevjobs import detail, row


def test_swissdevjobs_double_cycle_preserves_raw_and_counts(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_swissdevjobs as module

    monkeypatch.setattr(module, "DETAIL_PAUSE_SECONDS", 0)
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    raw = row()
    requests = []

    def transport(request):
        requests.append(request.url.path)
        body = json.dumps([raw]) if request.url.path == "/api/jobsLight" else detail()
        return httpx.Response(200, content=body.encode())

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='swissdevjobs' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            first = await run_scope(
                scope_id,
                get_provider("swissdevjobs"),
                RawListingSink(),
                client,
                factory,
            )
            second = await run_scope(
                scope_id,
                get_provider("swissdevjobs"),
                RawListingSink(),
                client,
                factory,
            )
        assert first.status == second.status == "ok"
        assert first.listings == second.listings == 1
        assert requests == ["/api/jobsLight", "/jobs/job-1", "/api/jobsLight"]

        async with factory() as session:
            state = (
                (
                    await session.execute(
                        sa.text(
                            "SELECT cursor FROM source_scope_state WHERE scope_id=:scope"
                        ),
                        {"scope": scope_id},
                    )
                )
                .one()
                .cursor
            )
            stored = (
                await session.execute(
                    sa.text(
                        "SELECT r.raw, o.content FROM source_listing_revisions r "
                        "JOIN source_listing_incarnations i ON i.id=r.incarnation_id "
                        "JOIN vacancies v ON v.id=i.vacancy_id "
                        "JOIN offer_revisions o ON o.id=v.current_offer_revision_id "
                        "JOIN source_listings sl ON sl.id=i.source_listing_id "
                        "JOIN sources s ON s.id=sl.source_id "
                        "WHERE s.name='swissdevjobs'"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "swissdevjobs", item_limit=300, top_n=500, lease=60
            )

        assert state["descriptions"] == {"job-1": "Build APIs &amp; ship."}
        assert len(stored) == 1
        assert stored[0].raw == {
            **raw,
            "_core_description": "Build APIs &amp; ship.",
        }
        assert stored[0].content["description"] == "Build APIs &amp; ship."
        assert len(stored[0].content["tags"]) == 10
        assert aggregate["items"][0]["published_at"] == raw["activeFrom"]
        assert aggregate["counts"]["tags"] == {
            f"Tech {number}": 1 for number in range(12)
        }

    asyncio.run(run())
