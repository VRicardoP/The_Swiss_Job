"""Native RSS -> admission -> real sink -> canonical revision, repeatable."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import sqlalchemy as sa

from jobhunt_core.harvest.providers import get_provider
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.tests.test_integration_harvest import db, pytestmark, _seed_scopes
from jobhunt_core.tests.test_native_rss_providers import SOURCES, feed


@pytest.mark.parametrize("source", SOURCES)
def test_rss_refreshes_existing_old_listing_without_duplicate_or_new_revision(db, source):
    factory, made = db
    sid, = _seed_scopes(factory, made, 1)
    # First harvest admits this real upstream timestamp; then tighten the window.
    date = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    body = feed().replace(b"Fri, 18 Sep 2026 12:00:00 +0000", date.encode())

    async def run():
        async with factory() as s:
            await s.execute(sa.text("UPDATE sources SET name=:n WHERE id=:s"), {"n": source, "s": made["source"]})
            await s.execute(sa.text("UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:s"),
                            {"p": json.dumps({"admission_window_days": 14}), "s": sid})
            await s.commit()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body)
        )) as http:
            first = await run_scope(sid, get_provider(source), RawListingSink(), http, factory)
            assert first.status == "ok" and first.listings == 1
            async with factory() as s:
                await s.execute(sa.text("UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:s"),
                                {"p": json.dumps({"admission_window_days": 7}), "s": sid})
                await s.commit()
            second = await run_scope(sid, get_provider(source), RawListingSink(), http, factory)
            assert second.status == "ok" and second.detail["refreshed"] == 1
        async with factory() as s:
            rows = (await s.execute(sa.text("""
                SELECT r.raw AS payload, o.content FROM source_listings l
                  JOIN source_listing_incarnations i ON i.source_listing_id=l.id
                  JOIN source_listing_revisions r ON r.incarnation_id=i.id
                  JOIN vacancies v ON v.id=i.vacancy_id
                  JOIN offer_revisions o ON o.id=v.current_offer_revision_id
                 WHERE l.source_id=:s
            """), {"s": made["source"]})).all()
            assert len(rows) == 1
            assert rows[0].payload["pubDate"] == date
            assert rows[0].content["description"] == "English writer in Geneva"
    asyncio.run(run())
