"""DynamiteJobs survives the real fenced sink twice with legacy dimensions."""

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
from jobhunt_core.tests.test_native_dynamitejobs import detail, page, row


def test_dynamitejobs_double_cycle_preserves_recent_and_dimensions(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_dynamitejobs as module

    monkeypatch.setattr(module, "REQUEST_PAUSE_S", 0)
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    rows = [row(1), row(2)]
    requests = []

    def transport(request):
        requests.append(request.url.path)
        if request.url.path.startswith("/category/"):
            body = rows if request.url.path.endswith(module.CATEGORY_SLUGS[0]) else []
            return httpx.Response(200, text=page(body))
        return httpx.Response(200, text=detail())

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='dynamitejobs' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            first = await run_scope(
                scope_id,
                get_provider("dynamitejobs"),
                RawListingSink(),
                client,
                factory,
            )
            second = await run_scope(
                scope_id,
                get_provider("dynamitejobs"),
                RawListingSink(),
                client,
                factory,
            )
        assert first.status == second.status == "ok"
        assert first.listings == second.listings == 2
        assert len(requests) == 42

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
                        "WHERE s.name='dynamitejobs' ORDER BY sl.external_id"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "dynamitejobs", item_limit=300, top_n=1000, lease=60
            )

        assert state["descriptions"] == {
            "job-1": "Build APIs and ship.",
            "job-2": "Build APIs and ship.",
        }
        assert len(stored) == 2
        assert stored[0].raw["_core_statistics_location"] == "Latin America"
        assert stored[0].content["location"] == "Latin America"
        assert aggregate["total"] == 2
        assert [item["external_id"] for item in aggregate["items"]] == [
            "job-2",
            "job-1",
        ]
        assert aggregate["items"][0]["description"] == "Build APIs and ship."
        assert (
            aggregate["items"][0]["published_at"] == stored[1].raw["_core_published_at"]
        )
        assert aggregate["counts"]["locations"] == {"Latin America": 2}
        assert aggregate["counts"]["tags"] == {
            f"skill {number}": 2 for number in range(12)
        }

    asyncio.run(run())
