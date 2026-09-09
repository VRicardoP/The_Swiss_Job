"""Existing BFF total/offset contract without downloading the entire corpus."""
import pytest

from jobhunt_core.tests import test_integration_api as api

db = api.db
pytestmark = api.pytestmark


def test_offset_total_and_bounded_hydration(db, monkeypatch):
    from jobhunt_core.api import v1
    factory, created = db
    term, _, token = api._seed_catalog(factory, created, n=5)
    base = f'/v1/vacancies?q={term}'
    full = api._api(factory, base, token=token).json()['items']
    observed = []
    original = v1._vacancy_dtos

    async def observe(session, ids):
        observed.append(len(ids))
        return await original(session, ids)

    monkeypatch.setattr(v1, '_vacancy_dtos', observe)
    for offset, expected in ((0, full[:2]), (3, full[3:5]), (5, []), (100, [])):
        response = api._api(factory, base + f'&limit=2&offset={offset}', token=token)
        assert response.status_code == 200
        body = response.json()
        assert body['total'] == 5
        assert body['items'] == expected
        assert bool(body['next_cursor']) == (offset == 0)
    assert observed == [2, 2, 0, 0]


def test_offset_filters_empty_total_and_etag(db):
    factory, created = db
    term, vacancies, token = api._seed_catalog(factory, created, n=3)
    api._enrich_content(factory, vacancies[term+'v0'], {'remote': True, 'location': 'Zurich, Switzerland'})
    base = f'/v1/vacancies?q={term}&source=ARBEITNOW&remote=true&city=zurich&country=switzerland&offset=0&limit=1'
    response = api._api(factory, base, token=token)
    assert response.json()['total'] == 1
    assert len(response.json()['items']) == 1
    assert response.json()['next_cursor'] is None
    cached = api._api(factory, base, token=token, headers={'If-None-Match': response.headers['etag']})
    assert cached.status_code == 304
    empty = api._api(factory, base.replace('city=zurich', 'city=missing'), token=token).json()
    assert empty == {'items': [], 'next_cursor': None, 'total': 0}
    # Same visible first page, changed total: stale ETag must NOT validate.
    api._enrich_content(factory, vacancies[term+'v1'], {'remote': True, 'location': 'Zurich, Switzerland'})
    changed = api._api(factory, base, token=token, headers={'If-None-Match': response.headers['etag']})
    assert changed.status_code == 200 and changed.json()['total'] == 2


def test_remote_filter_remains_indexable_with_generic_prepared_plans(db):
    # A boolean parameter prevents generic plans from proving the partial
    # index predicate. HTTP validation already narrows the input to bool.
    import asyncio
    import sqlalchemy as sa
    from jobhunt_core.api.v1 import _catalog_filter_sql
    factory, _ = db
    for value in (True, False):
        _, where, params = _catalog_filter_sql(None, None, value, None, None)
        assert "remote" not in params
        assert "(o.content->>'remote')::boolean = " + str(value).lower() in where
    async def check():
        async with factory() as session:
            result = await session.scalar(sa.text(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relname='ix_offer_revisions_catalog_remote' AND n.nspname=current_schema()"))
            assert result is True
    asyncio.run(check())


def test_offset_rejects_mixed_cursor_and_negative_offset(db):
    factory, created = db
    term, _, token = api._seed_catalog(factory, created, n=3)
    base = f'/v1/vacancies?q={term}&limit=1'
    cursor = api._api(factory, base, token=token).json()['next_cursor']
    assert api._api(factory, base + '&offset=0&cursor=' + cursor, token=token).status_code == 400
    for offset in (-1, 9223372036854775808):
        assert api._api(factory, base + f'&offset={offset}', token=token).status_code == 400
