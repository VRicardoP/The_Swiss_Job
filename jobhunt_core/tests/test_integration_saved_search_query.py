"""The real sink and Postgres FTS must preserve the existing search contract."""

import asyncio

import sqlalchemy as sa

from jobhunt_core.harvest.providers import legacy_shadow
from jobhunt_core.harvest.types import RawListing
from jobhunt_core.saved_search_query import matching_vacancies
from jobhunt_core.tests.test_integration_offer import db, pytestmark, _seed, _sink, _vacancy_state  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_full_text_and_structured_filters_over_canonical_offers(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    source = "legacy:search-query"
    legacy_shadow.ensure_registered(source)
    scope = _seed(factory, made, source)
    base = {
        "title": "Developer", "description": "Python backend", "company_name": "Example",
        "remote": True, "canton": "ZH", "language": "de", "seniority": "senior",
        "contract_type": "full_time", "salary_min_chf": 80000, "salary_max_chf": 120000,
    }
    raws = {
        "wanted": base,
        "tag-only": {**base, "description": "Cooking", "tags": ["python", "backend"]},
        "onsite": {**base, "remote": False},
        "unknown": {**base, "canton": None, "salary_min_chf": None, "salary_max_chf": None},
        "archived": base, "ended": base,
    }
    _sink(factory, scope, [RawListing(key, f"https://search.test/{key}", raw) for key, raw in raws.items()])
    ids = {key: _vacancy_state(factory, key).vac for key in raws}

    async def run():
        async with factory() as session:
            await session.execute(sa.text("UPDATE vacancies SET archived_at=clock_timestamp() WHERE id=:id"), {"id": ids["archived"]})
            await session.execute(sa.text("UPDATE source_listing_incarnations SET ended_at=clock_timestamp() WHERE vacancy_id=:id"), {"id": ids["ended"]})

            async def matches(filters):
                return {r.vacancy_id for r in await matching_vacancies(session, filters)}

            assert await matches({"q": "Python backend"}) == {ids[k] for k in ("wanted", "onsite", "unknown")}
            all_filters = {
                "q": "Python backend", "source": "search-query, missing", "canton": " be, zh ",
                "remote_only": True, "language": "de", "seniority": "senior",
                "contract_type": "full_time", "salary_min": 100000, "salary_max": 150000,
            }
            assert await matches(all_filters) == {ids["wanted"]}
            for key, value in (("canton", "BE"), ("language", "fr"), ("seniority", "junior"),
                               ("contract_type", "part_time"), ("salary_min", 120001),
                               ("salary_max", 79999), ("source", "other")):
                assert await matches({**all_filters, key: value}) == set(), key
            assert await matches({"q": "no-such-term"}) == set()
            assert await matches({"q": "' OR true --"}) == set()
            await session.rollback()
    asyncio.run(run())


def test_metadata_update_and_replay_do_not_change_embedding_identity(db):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = db
    source = "legacy:search-replay"
    legacy_shadow.ensure_registered(source)
    scope = _seed(factory, made, source)
    base = {"title": "Analyst", "company_name": "Example", "description": "Data"}
    listing = RawListing("search-replay", "https://search.test/replay", base)
    _sink(factory, scope, [listing])
    first = _vacancy_state(factory, listing.external_id)
    enriched = RawListing(listing.external_id, listing.url, {**base, "canton": "ZH", "salary_min_chf": 80000})
    _sink(factory, scope, [enriched])
    second = _vacancy_state(factory, listing.external_id)
    assert first.cur != second.cur and first.text_hash == second.text_hash
    assert second.content["canton"] == "ZH" and second.content["salary_min_chf"] == 80000
    _sink(factory, scope, [enriched])
    assert _vacancy_state(factory, listing.external_id).cur == second.cur
    _sink(factory, scope, [listing])
    assert _vacancy_state(factory, listing.external_id).cur == first.cur
