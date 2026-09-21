"""The two ported scrapers use the same fenced admission/sink path as the rest.

These are the only sources of the handover whose producer had to be written
from scratch, so the whole chain — provider, admission window, raw persistence,
canonical revision, idempotent replay — is exercised against a real database,
not just the parser.
"""

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
from jobhunt_core.tests.test_native_financejobs import JOB, page as fj_page
from jobhunt_core.tests.test_native_irishjobs import ITEM, page as ij_page


def _bodies(source, date):
    if source == "financejobs":
        return (fj_page([{**JOB, "datePosted": date}]), fj_page([]))
    return (ij_page([{**ITEM, "datePosted": date}]), ij_page([]))


@pytest.mark.parametrize("source", ["financejobs", "irishjobs"])
def test_admission_raw_and_canonical_roundtrip(db, source, monkeypatch):
    monkeypatch.setattr(
        f"jobhunt_core.harvest.providers.native_{source}.PAGE_PAUSE_S", 0)
    factory, made = db
    sid, = _seed_scopes(factory, made, 1)
    date = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    first_page, empty = _bodies(source, date)

    def transport(request):
        return httpx.Response(
            200, content=first_page if int(request.url.params["page"]) == 1 else empty)

    async def run():
        async with factory() as s:
            await s.execute(sa.text("UPDATE sources SET name=:n WHERE id=:s"),
                            {"n": source, "s": made["source"]})
            await s.execute(
                sa.text("UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:s"),
                {"p": json.dumps({"admission_window_days": 14}), "s": sid})
            await s.commit()
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            first = await run_scope(sid, get_provider(source), RawListingSink(), http, factory)
            assert first.status == "ok" and first.listings == 1

            # Narrowing the window must not re-admit it as new, but must keep
            # refreshing the identity we already know.
            async with factory() as s:
                await s.execute(
                    sa.text("UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:s"),
                    {"p": json.dumps({"admission_window_days": 7}), "s": sid})
                await s.commit()
            second = await run_scope(sid, get_provider(source), RawListingSink(), http, factory)
            assert second.status == "ok" and second.detail["refreshed"] == 1

        async with factory() as s:
            rows = (await s.execute(sa.text(
                "SELECT l.external_id, i.url, o.content->>'title' AS title, "
                "       r.raw IS NOT NULL AS raw_kept "
                "FROM source_listings l "
                "JOIN source_listing_incarnations i ON i.source_listing_id=l.id "
                "JOIN vacancies v ON v.primary_incarnation_id=i.id "
                "JOIN offer_revisions o ON o.id=v.current_offer_revision_id "
                "JOIN offer_revision_sources os ON os.offer_revision_id=o.id "
                "JOIN source_listing_revisions r ON r.id=os.source_listing_revision_id "
                "WHERE l.source_id=:src"), {"src": made["source"]})).all()
            assert len(rows) == 1, "replay must not create a second vacancy"
            row, = rows
            assert row.raw_kept and row.title and row.external_id
            assert row.url.startswith("https://")

    asyncio.run(run())


@pytest.mark.parametrize("source", ["financejobs", "irishjobs"])
def test_offer_outside_the_window_is_not_admitted(db, source, monkeypatch):
    """A date the portal declares old must not enter as a fresh offering."""
    monkeypatch.setattr(
        f"jobhunt_core.harvest.providers.native_{source}.PAGE_PAUSE_S", 0)
    factory, made = db
    sid, = _seed_scopes(factory, made, 1)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    first_page, empty = _bodies(source, old)

    async def run():
        async with factory() as s:
            await s.execute(sa.text("UPDATE sources SET name=:n WHERE id=:s"),
                            {"n": source, "s": made["source"]})
            await s.execute(
                sa.text("UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:s"),
                {"p": json.dumps({"admission_window_days": 7}), "s": sid})
            await s.commit()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=first_page if int(request.url.params["page"]) == 1 else empty)
        )) as http:
            result = await run_scope(sid, get_provider(source), RawListingSink(), http, factory)
        assert result.status == "ok" and result.listings == 0

    asyncio.run(run())
