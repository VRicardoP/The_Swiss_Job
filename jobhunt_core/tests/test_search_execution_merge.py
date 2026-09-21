"""A canonical merge cannot re-notify an already observed opportunity."""

import asyncio

import sqlalchemy as sa

from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.tests.test_integration_search_execution import (
    db, pytestmark, corpus, new_search, listing, run, state, _vacancy_state,
)


def test_seen_loser_propagates_through_merge_chain(db):
    factory, made = db
    scope, listings = corpus(factory, made, "Python developer")
    loser = _vacancy_state(factory, listings[0].external_id).vac

    async def go():
        async with factory() as s:
            sid, _, tenant, _ = await new_search(s, made)
            assert (await run(s, sid, tenant))["matches"] == 1
            await RawListingSink().handle(s, scope, (listing("middle"), listing("winner")))
            ids = dict((await s.execute(sa.text("""
                SELECT sl.external_id, i.vacancy_id FROM source_listings sl
                JOIN source_listing_incarnations i ON i.source_listing_id=sl.id
                WHERE sl.external_id IN ('middle','winner') AND i.ended_at IS NULL
            """))).all())
            await s.execute(sa.text("UPDATE vacancies SET merged_into=:w WHERE id=:v"), {"v": loser, "w": ids["middle"]})
            await s.execute(sa.text("UPDATE vacancies SET merged_into=:w WHERE id=:v"), {"v": ids["middle"], "w": ids["winner"]})
            await s.commit()
            assert (await run(s, sid, tenant))["matches"] == 0
            st = await state(s, sid)
            assert st.total_matches == st.events == 1
    asyncio.run(go())
