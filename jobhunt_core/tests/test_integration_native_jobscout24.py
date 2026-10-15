"""JobScout24 survives the real fenced sink twice with legacy dimensions."""

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
from jobhunt_core.tests.test_native_jobscout24 import card, detail, page


def test_jobscout24_double_cycle_preserves_recent_and_dimensions(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_jobscout24 as module

    monkeypatch.setattr(module, "REQUEST_PAUSE_S", 0)
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    listing = page(card(1, date="5 d") + card(2, date="1 d"))
    requests = []

    def transport(request):
        requests.append(str(request.url))
        if request.url.path.startswith("/en/jobs/"):
            first = request.url.path.split("/")[3] == module.KEYWORDS[0]
            number = int(request.url.params.get("p", 1))
            body = listing if first and number == 1 else page()
            return httpx.Response(200, text=body)
        return httpx.Response(200, text=detail())

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='jobscout24' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            first = await run_scope(
                scope_id,
                get_provider("jobscout24"),
                RawListingSink(),
                client,
                factory,
            )
            second = await run_scope(
                scope_id,
                get_provider("jobscout24"),
                RawListingSink(),
                client,
                factory,
            )
        assert first.status == second.status == "ok"
        assert first.listings == second.listings == 2
        # Six listing pages per cycle (the first keyword exhausts two), two
        # detail pages on the cold cycle and none on the second.
        assert len(requests) == 14

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
                        "WHERE s.name='jobscout24' ORDER BY sl.external_id"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "jobscout24", item_limit=300, top_n=500, lease=60
            )

        assert state["descriptions"] == {
            "job-1": "Build APIs and ship.",
            "job-2": "Build APIs and ship.",
        }
        assert len(stored) == 2
        assert stored[0].content["location"] == "Zürich"
        assert stored[0].content["employment_type"] == "100%"
        assert aggregate["total"] == 2
        # The newest card first, exactly as the producer's served order.
        assert [item["external_id"] for item in aggregate["items"]] == [
            "job-2",
            "job-1",
        ]
        assert aggregate["items"][0]["description"] == "Build APIs and ship."
        assert aggregate["items"][0]["published_at"] == stored[1].raw["date"]
        assert aggregate["items"][0]["tags"] == [
            "Large companies",
            "developer",
            "python",
        ]
        assert aggregate["counts"]["locations"] == {"Zürich": 2}
        assert aggregate["counts"]["tags"] == {
            "large companies": 2,
            "developer": 2,
            "python": 2,
        }

    asyncio.run(run())
