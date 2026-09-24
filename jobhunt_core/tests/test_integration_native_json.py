"""Native JSON raw/admission/sink round trip without a second writer."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import sqlalchemy as sa

from jobhunt_core.harvest.admission import DATE_FIELDS
from jobhunt_core.harvest.providers import get_provider
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.tests.test_integration_harvest import db, pytestmark, _seed_scopes  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


@pytest.mark.parametrize("source", ["remotive", "workingnomads", "jobicy"])
def test_native_json_preserves_raw_and_refreshes_old_known_identity(db, source):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    sid, = _seed_scopes(factory, made, 1)
    stamp = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    raw = {"id": 123, "url": "https://native-json.example/123", "title": "English teacher",
           "jobTitle": "English teacher", "company_name": "School", "companyName": "School",
           "description": "<p>TEFL &amp; English</p>", "jobDescription": "<p>TEFL &amp; English</p>",
           DATE_FIELDS[source]: stamp, "extra": {"retained": True}}
    body = [raw] if source == "workingnomads" else {"jobs": [raw]}

    async def run():
        async with factory() as s:
            await s.execute(sa.text("UPDATE sources SET name=:n WHERE id=:s"), {"n": source, "s": made["source"]})
            await s.execute(sa.text("UPDATE harvest_scopes SET params=CAST(:p AS jsonb) WHERE id=:s"),
                            {"p": json.dumps({"admission_window_days": 14}), "s": sid})
            await s.commit()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body)
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
                SELECT r.raw, o.content FROM source_listings l
                JOIN source_listing_incarnations i ON i.source_listing_id=l.id
                JOIN source_listing_revisions r ON r.incarnation_id=i.id
                JOIN vacancies v ON v.id=i.vacancy_id
                JOIN offer_revisions o ON o.id=v.current_offer_revision_id
                WHERE l.source_id=:s
            """), {"s": made["source"]})).all()
            assert len(rows) == 1 and rows[0].raw == raw
            assert rows[0].content["description"] == "TEFL &amp; English"
            assert {"english", "tefl"} <= set(rows[0].content["tags"])
            if source == "workingnomads":
                assert rows[0].content["language"] == "en"
    asyncio.run(run())
