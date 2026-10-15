"""myScience survives the real fenced sink twice with its portal date."""

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
from jobhunt_core.tests.test_native_myscience import detail, listing, record


def test_myscience_double_cycle_keeps_one_identity_per_offer(db, monkeypatch):  # noqa: F811
    from jobhunt_core.harvest.providers import native_myscience as module

    monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    # The portal repeats a record across pages and renames a slug between
    # cycles: the identity must not move.
    first_page = listing(record(1, date="2026-10-09") + record(2))
    second_page = listing(record(2) + record(3, date=""))
    cycle = {"n": 0}
    requests = []

    def transport(request):
        requests.append(str(request.url))
        if request.url.path == "/jobs":
            number = int(request.url.params.get("p", 1))
            if number == 1:
                return httpx.Response(200, text=first_page)
            if number == 2:
                return httpx.Response(200, text=second_page)
            return httpx.Response(200, text=listing())
        return httpx.Response(200, text=detail())

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='myscience' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            first = await run_scope(
                scope_id,
                get_provider("myscience"),
                RawListingSink(),
                client,
                factory,
            )
            cycle["n"] = 1
            second = await run_scope(
                scope_id,
                get_provider("myscience"),
                RawListingSink(),
                client,
                factory,
            )
        assert first.status == second.status == "ok"
        assert first.listings == second.listings == 3

        async with factory() as session:
            stored = (
                await session.execute(
                    sa.text(
                        "SELECT sl.external_id, i.url, r.raw, o.content "
                        "FROM source_listing_revisions r "
                        "JOIN source_listing_incarnations i ON i.id=r.incarnation_id "
                        "JOIN vacancies v ON v.id=i.vacancy_id "
                        "JOIN offer_revisions o ON o.id=v.current_offer_revision_id "
                        "JOIN source_listings sl ON sl.id=i.source_listing_id "
                        "JOIN sources s ON s.id=sl.source_id "
                        "WHERE s.name='myscience' ORDER BY sl.external_id"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "myscience", item_limit=300, top_n=500, lease=60
            )

        # Three offers, one revision each: the second cycle changed nothing.
        assert [row.external_id for row in stored] == [
            "id:70451",
            "id:70452",
            "id:70453",
        ]
        assert len(stored) == 3
        assert stored[0].content["canton"] == "ZH"
        assert stored[0].content["description"] == "Build things\nand ship in English"
        assert aggregate["total"] == 3
        published = {
            item["external_id"]: item["published_at"] for item in aggregate["items"]
        }
        # The portal's own date travels; a record without one stays unknown
        # instead of borrowing the ingestion time.
        assert published["id:70451"] == "2026-10-09"
        assert published["id:70452"] == "2026-10-14"
        assert published["id:70453"] is None
        assert aggregate["counts"]["locations"] == {"Zürich": 3}

    asyncio.run(run())
