"""Missing public offers go through the sink, atomically and without alerts."""
import asyncio
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core.import_search_corpus import seed_missing_offers
from jobhunt_core.import_swissjob_searches import SearchMigrationError
from jobhunt_core.shadow.projector import JOB_PAYLOAD_MAP
from jobhunt_core.tests.test_integration_offer import db, pytestmark


def rows(source):
    row = dict.fromkeys(JOB_PAYLOAD_MAP)
    row.update(hash=uuid.uuid4().hex, source=source, url='https://missing-search.test/'+uuid.uuid4().hex,
        apply_url=None, is_active=True, duplicate_of=None, title='Python developer', company='Example',
        language='en', tags=[], remote=False)
    return [row]


async def track(session, made, source):
    for src,scope in (await session.execute(sa.text(
        "SELECT s.id,hs.id FROM sources s JOIN harvest_scopes hs ON hs.source_id=s.id WHERE s.name=:n"
    ), {"n": 'legacy:'+source})).all():
        made['sources'].append(src)
        made['scopes'].append(scope)


def test_missing_offer_is_presentable_and_retry_does_not_duplicate(db):
    factory, made = db
    source = 'missing-search-'+uuid.uuid4().hex[:8]
    batch = rows(source)

    async def go():
        async with factory() as session:
            first = await seed_missing_offers(session, source, batch)
            await track(session,made,source)
            assert first['inserted_listings'] == 1
            assert len(first['provenance']['vacancies']) == 1
            await session.commit()
            second = await seed_missing_offers(session, source, batch)
            assert second == {'resolved':1,'inserted_listings':0,'provenance':{}}
            await session.commit()
            content = await session.scalar(sa.text('SELECT content FROM offer_revisions WHERE vacancy_id=:v'),
                {'v':uuid.UUID(first['provenance']['vacancies'][0])})
            assert content['language'] == 'en'
            assert not await session.scalar(sa.text("SELECT enabled FROM harvest_scopes WHERE id=:id"), {'id':made['scopes'][0]})
    asyncio.run(go())


@pytest.mark.parametrize('defect', ['inactive','duplicate','missing_field','quarantined'])
def test_invalid_or_quarantined_rows_do_not_leave_partial_source(db, defect):
    factory, _ = db
    source = 'invalid-search-'+uuid.uuid4().hex[:8]
    batch = rows(source)
    if defect == 'inactive': batch[0]['is_active'] = False
    elif defect == 'duplicate': batch[0]['duplicate_of'] = 'other'
    elif defect == 'missing_field': del batch[0]['description']
    else: batch[0]['title'] = '\x00'

    async def go():
        async with factory() as session:
            with pytest.raises(SearchMigrationError):
                await seed_missing_offers(session,source,batch)
            assert await session.scalar(sa.text('SELECT count(*) FROM sources WHERE name=:n'), {'n':'legacy:'+source}) == 0
            await session.commit()
    asyncio.run(go())


def test_existing_closed_slot_is_not_reopened(db):
    factory, made = db
    source = 'closed-search-'+uuid.uuid4().hex[:8]
    batch = rows(source)

    async def go():
        async with factory() as session:
            first = await seed_missing_offers(session,source,batch)
            await track(session,made,source)
            await session.commit()
            vid = uuid.UUID(first['provenance']['vacancies'][0])
            await session.execute(sa.text('UPDATE vacancies SET archived_at=clock_timestamp() WHERE id=:id'), {'id':vid})
            await session.commit()
            with pytest.raises(SearchMigrationError,match='existing slot'):
                await seed_missing_offers(session,source,batch)
            assert await session.scalar(sa.text('SELECT archived_at IS NOT NULL FROM vacancies WHERE id=:id'), {'id':vid})
    asyncio.run(go())
