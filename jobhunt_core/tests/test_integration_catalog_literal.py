"""Indexed search keeps the old literal substring semantics, including wildcards."""
import asyncio
from urllib.parse import urlencode

import pytest
import sqlalchemy as sa

from jobhunt_core.tests import test_integration_api as api

db = api.db
pytestmark = api.pytestmark


def test_indexed_catalog_matches_literal_substring_reference(db):
    factory, created = db
    term, vacancies, token = api._seed_catalog(factory, created, n=3)
    api._enrich_content(factory, vacancies[term+'v0'], {'title': term+r' literal%_\ C++ MÜNCHEN'})
    api._enrich_content(factory, vacancies[term+'v1'], {'title': term+' literalZZ C#', 'company':term+' MÜNCHEN'})

    async def expected(query):
        async with factory() as session:
            return set(str(row) for row in (await session.scalars(sa.text(
                "SELECT v.id FROM vacancies v JOIN offer_revisions o ON o.id=v.current_offer_revision_id "
                "WHERE v.archived_at IS NULL AND v.merged_into IS NULL AND "
                "(position(lower(:q) in lower(coalesce(o.content->>'title','')))>0 "
                "OR position(lower(:q) in lower(coalesce(o.content->>'company','')))>0)"
            ), {'q':query.strip()})).all())

    for suffix in ('', ' literal%', ' literal_', ' literal\\', ' literal%_\\', ' literalZZ', ' MÜNCHEN', ' missing'):
        query = '  '+term+suffix+'  '
        response = api._api(factory, '/v1/vacancies?'+urlencode({'q':query,'offset':0,'limit':100}), token=token)
        assert response.status_code == 200
        actual = response.json()
        wanted = asyncio.run(expected(query))
        assert {row['id'] for row in actual['items']} == wanted
        assert actual['total'] == len(wanted)


def test_catalog_text_indexes_present_and_query_is_indexable(db):
    from jobhunt_core.api.v1 import _catalog_filter_sql
    factory, _ = db
    _, conditions, params = _catalog_filter_sql('python',None,None,None,None)
    assert 'LIKE' in conditions[0]
    async def check():
        async with factory() as session:
            names = set((await session.scalars(sa.text(
                "SELECT c.relname FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() "
                "AND i.indisvalid AND c.relname IN ('ix_offer_revisions_catalog_title','ix_offer_revisions_catalog_company')"
            ))).all())
            assert len(names) == 2
    asyncio.run(check())
