"""Raw date admission and immutable canonical content, through the real sink."""
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
from jobhunt_core.tests.test_native_nav import hit, page
from jobhunt_core.tests.test_native_thehub import item, detail
from jobhunt_core.tests.test_native_jobgether import raw


@pytest.mark.parametrize("source", ["nav_arbeidsplassen", "thehub", "jobgether"])
def test_native_scopes_admit_refresh_and_preserve_raw(db, source):
    factory, made = db
    sid, = _seed_scopes(factory, made, 1)
    date = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    nav = hit()
    nav["_source"]["published"] = date
    hub = {**detail(), "createdAt": date}
    jobgether = raw(createdAt=date)
    fail_detail = False

    def transport(request):
        if source == "nav_arbeidsplassen":
            body = page([nav])
        elif source == "jobgether":
            body = {"data": [jobgether], "maxPages": 1}
        elif request.url.path == "/v2/jobs":
            body = {"docs": [item()], "pages": 1}
        elif fail_detail:
            return httpx.Response(503)
        else:
            body = hub
        return httpx.Response(200, content=json.dumps(body).encode())

    async def run():
        nonlocal fail_detail
        params = {"remote": "Kun hjemmekontor"} if source == "nav_arbeidsplassen" else {}
        async with factory() as session:
            await session.execute(sa.text("UPDATE sources SET name=:n WHERE id=:s"),
                                  {"n": source, "s": made["source"]})
            await session.execute(sa.text("UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:s"),
                                  {"p": json.dumps({**params, "admission_window_days": 14}), "s": sid})
            await session.commit()
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await run_scope(sid, get_provider(source), RawListingSink(), client, factory)
            assert result.status == "ok" and result.listings == 1
            async with factory() as session:
                await session.execute(sa.text("UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:s"),
                                      {"p": json.dumps({**params, "admission_window_days": 7}), "s": sid})
                await session.commit()
            again = await run_scope(sid, get_provider(source), RawListingSink(), client, factory)
            assert again.status == "ok" and again.detail["refreshed"] == 1
            if source == "thehub":
                fail_detail = True
                failed = await run_scope(sid, get_provider(source), RawListingSink(), client, factory)
                assert failed.status == "error"
        async with factory() as session:
            rows = (await session.execute(sa.text("""
                SELECT r.raw, o.content FROM source_listings l
                JOIN source_listing_incarnations i ON i.source_listing_id=l.id
                JOIN source_listing_revisions r ON r.incarnation_id=i.id
                JOIN vacancies v ON v.id=i.vacancy_id
                JOIN offer_revisions o ON o.id=v.current_offer_revision_id
                WHERE l.source_id=:s
            """), {"s": made["source"]})).all()
            expected = {"nav_arbeidsplassen": nav, "thehub": hub, "jobgether": jobgether}
            assert len(rows) == 1 and rows[0].raw == expected[source]
            if source == "jobgether":
                assert rows[0].content["description"] is None  # sink canonizes absent text
                assert rows[0].content["salary"] == "80-120 EUR"
            else:
                assert "TEFL" in rows[0].content["description"]
    asyncio.run(run())
