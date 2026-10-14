"""Job-Room survives the real fenced sink twice with legacy-compatible counts."""

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
from jobhunt_core.tests.test_native_jobroom import row


def test_jobroom_double_cycle_preserves_raw_and_counts(db):  # noqa: F811
    factory, made = db
    (scope_id,) = _seed_scopes(factory, made, 1)
    raw = row()
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json=[raw])

    async def run():
        async with factory() as session:
            await session.execute(
                sa.text("UPDATE sources SET name='jobroom' WHERE id=:id"),
                {"id": made["source"]},
            )
            await session.commit()

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            first = await run_scope(
                scope_id,
                get_provider("jobroom"),
                RawListingSink(),
                client,
                factory,
            )
            second = await run_scope(
                scope_id,
                get_provider("jobroom"),
                RawListingSink(),
                client,
                factory,
            )
        assert first.status == second.status == "ok"
        assert first.listings == second.listings == 1
        assert len(requests) == 2 and all(
            request.method == "POST" for request in requests
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
                        "WHERE s.name='jobroom'"
                    )
                )
            ).all()
            aggregate = await fetch_source_aggregate(
                session, "jobroom", item_limit=300, top_n=500, lease=60
            )

        expected_skills = [
            "backend",
            "python",
            "javascript",
            "typescript",
            "react",
            "node.js",
            "sql",
            "redis",
            "docker",
            "kubernetes",
            "aws",
            "linux",
            "terraform",
        ]
        assert len(stored) == 1
        assert stored[0].raw == {**raw, "_core_statistics_tags": expected_skills}
        assert stored[0].content["description"].startswith("Python JavaScript")
        assert len(stored[0].content["tags"]) == 10
        assert aggregate["items"][0]["published_at"] == "2026-10-13"
        assert aggregate["items"][0]["tags"] == stored[0].content["tags"]
        assert aggregate["counts"]["locations"] == {"Zürich, ZH": 1}
        assert aggregate["counts"]["tags"] == {tag: 1 for tag in expected_skills}
        assert "de" not in aggregate["counts"]["tags"]

    asyncio.run(run())
