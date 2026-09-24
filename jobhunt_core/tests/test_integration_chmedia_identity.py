"""Two real portal positions must survive a shared employer application page."""
import asyncio
import json

import httpx
import sqlalchemy as sa

from jobhunt_core.harvest.providers import get_provider
from jobhunt_core.harvest.runner import run_scope
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.tests.test_integration_harvest import db, pytestmark, _seed_scopes  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.test_native_chmedia import raw


def test_shared_ats_id_and_application_url_do_not_drop_position(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    sid, = _seed_scopes(factory, made, 1)
    payload = {"pages": 1, "items": [
        raw(id=1098816, companyId=1825, externalId="691", title="English teacher",
            urlApplication="https://example.org/careers"),
        raw(id=1084785, companyId=1548, externalId="691", title="Math teacher",
            urlApplication="https://example.org/careers"),
    ]}

    async def run():
        async with factory() as session:
            await session.execute(sa.text("UPDATE sources SET name='ostjob' WHERE id=:s"),
                                  {"s": made["source"]})
            await session.commit()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=json.dumps(payload).encode())
        )) as client:
            for _ in range(2):
                outcome = await run_scope(sid, get_provider("ostjob"), RawListingSink(), client, factory)
                assert outcome.status == "ok"
        async with factory() as session:
            rows = (await session.execute(sa.text("""
                SELECT l.external_id, i.url, i.apply_url, i.vacancy_id, o.content->>'title' AS title
                FROM source_listings l JOIN source_listing_incarnations i ON i.source_listing_id=l.id
                JOIN vacancies v ON v.id=i.vacancy_id
                JOIN offer_revisions o ON o.id=v.current_offer_revision_id
                WHERE l.source_id=:s
            """), {"s": made["source"]})).mappings().all()
            assert len(rows) == len({row["vacancy_id"] for row in rows}) == 2
            assert {row["title"] for row in rows} == {"English teacher", "Math teacher"}
            assert all(row["apply_url"] == "https://example.org/careers" for row in rows)
            assert len({row["url"] for row in rows}) == 2
    asyncio.run(run())
