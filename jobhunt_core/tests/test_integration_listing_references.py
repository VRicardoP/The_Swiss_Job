"""Historical upstream aliases do not depend on the BFF's jobs table."""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core.tests import test_integration_api as api
from jobhunt_core.tests.test_integration_api import db  # noqa: F401

pytestmark = pytest.mark.skipif(not os.getenv("CORE_ADMIN_DATABASE_URL"), reason="requires PostgreSQL")


def test_resolve_listing_reference_and_ambiguity(db):
    factory, created = db
    _, vacs, _ = api._seed_matches(factory, created)
    _, _, token = api._issue(factory, created, "tenant-match", ["vacancies:read"])

    async def aliases():
        async with factory() as session:
            return (await session.execute(sa.text(
                "SELECT i.vacancy_id,sl.external_id,s.name AS source FROM source_listing_incarnations i "
                "JOIN source_listings sl ON sl.id=i.source_listing_id JOIN sources s ON s.id=sl.source_id "
                "WHERE i.vacancy_id=ANY(:ids) AND i.ended_at IS NULL ORDER BY i.vacancy_id"
            ), {"ids": list(vacs.values())})).all()
    refs = asyncio.run(aliases())
    assert len(refs) >= 2
    ref = refs[0]
    from urllib.parse import urlencode
    path = "/v1/listing-references?" + urlencode({"external_id": ref.external_id, "source": ref.source})
    response = api._api(factory, path, token=token)
    assert response.status_code == 200
    assert response.json() == {"vacancy_id": str(ref.vacancy_id)}
    assert api._api(factory, "/v1/listing-references?external_id=nonexistent-test-alias", token=token).status_code == 404
    assert api._api(factory, path).status_code == 401

    async def collide():
        async with factory() as session:
            sid, lid = uuid.uuid4(), uuid.uuid4()
            created["sources"].append(sid)
            await session.execute(sa.text("INSERT INTO sources(id,name,tier) VALUES(:id,:name,0)"), {"id": sid, "name": "alias-collision-" + sid.hex})
            await session.execute(sa.text("INSERT INTO source_listings(id,source_id,external_id,url_normalized) VALUES(:id,:s,:ref,:url)"),
                                  {"id": lid, "s": sid, "ref": ref.external_id, "url": "https://example.test/alias-" + lid.hex})
            await session.execute(sa.text("INSERT INTO source_listing_incarnations(id,source_listing_id,vacancy_id,seq,url) VALUES(:id,:l,:v,1,:u)"),
                                  {"id": uuid.uuid4(), "l": lid, "v": refs[1].vacancy_id, "u": "https://example.test/alias-" + lid.hex})
            await session.commit()
    asyncio.run(collide())
    ambiguous = "/v1/listing-references?" + urlencode({"external_id": ref.external_id})
    response = api._api(factory, ambiguous, token=token)
    assert response.status_code == 409
    assert response.json()["code"] == "ambiguous_reference"
    # An explicit source still resolves its own identity, without picking a winner.
    assert api._api(factory, path, token=token).json() == {"vacancy_id": str(ref.vacancy_id)}


@pytest.mark.parametrize("field", ["external_id", "source"])
def test_listing_reference_rejects_unstorable_query(db, field):
    from urllib.parse import urlencode
    factory, created = db
    _, _, token = api._issue(factory, created, "reference-validation", ["vacancies:read"])
    params = {"external_id": "valid", field: "bad\x00reference"}
    response = api._api(factory, "/v1/listing-references?" + urlencode(params), token=token)
    assert response.status_code == 400
