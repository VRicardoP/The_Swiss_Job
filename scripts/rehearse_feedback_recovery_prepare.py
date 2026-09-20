"""Prepare ONLY the isolated NAS copies for a post-cutover HTTP recovery rehearsal."""
import asyncio
import json
import logging
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from jobhunt_core import credentials
from jobhunt_core.database import SessionLocal, engine
from jobhunt_core.document_cutover import private_read, private_write
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import RawListing
from jobhunt_core.import_swissjob_feedback import apply_plan

ROOT = Path('/evidence')
SOURCE = 'postgresql+asyncpg://swissjob@127.0.0.1:5432/source_copy'
CORE = 'postgresql+asyncpg://jobhunt_core@127.0.0.1:5432/core_copy'


async def main():
    assert os.environ['CORE_DATABASE_URL'] == CORE
    state_path = ROOT/'feedback-recovery.private.json'
    async with SessionLocal() as db:
        if sys.argv[1:] == ['revoke']:
            state = private_read(state_path)
            await credentials.revoke_credential(db, state['env']['CORE_CONSUMER_KEY'].split('.')[0])
            await db.commit()
            print('copy-only feedback credential revoked')
            return
        assert not state_path.exists(), 'already prepared'
        plan = private_read(ROOT/'feedback-plan.private.json')
        result = await apply_plan(db, plan)
        sid, scope = uuid.uuid4(), uuid.uuid4()
        await db.execute(sa.text("INSERT INTO sources (id,name,tier) VALUES (:s,'rehearsal-feedback-native',0)"), {'s':sid})
        await db.execute(sa.text("INSERT INTO harvest_scopes (id,source_id,params,tier,enabled) VALUES (:id,:s,'{}',0,false)"), {'id':scope,'s':sid})
        raw = RawListing('post-cutover-native', 'https://feedback-rehearsal.invalid/native-only',
            {'title':'Synthetic recovery vacancy', 'company':'Rehearsal only', 'description':'Not a real application target'})
        await RawListingSink().handle(db, str(scope), (raw,))
        vid = (await db.execute(sa.text('SELECT i.vacancy_id FROM source_listings l JOIN source_listing_incarnations i ON i.source_listing_id=l.id WHERE l.source_id=:s AND i.ended_at IS NULL'), {'s':sid})).scalar_one()
        cid = (await db.execute(sa.text("SELECT id FROM consumers WHERE name='swissjob-shadow' AND active"))).scalar_one()
        key, secret = await credentials.create_credential(db,cid,
            ['applications:write','matches:read','vacancies:read','schools:read','schools:write'],
            expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
        source_engine = create_async_engine(SOURCE, poolclass=NullPool)
        try:
            async with async_sessionmaker(source_engine)() as source:
                count = await source.scalar(sa.text('SELECT count(*) FROM jobs WHERE url=:url'), {'url':raw.url})
                assert count == 0, 'native fixture has a local counterpart'
                bindings = (await source.execute(sa.text('SELECT user_id::text,core_profile_id::text FROM jobhunt_profile_map ORDER BY user_id'))).all()
                source_count = await source.scalar(sa.text('SELECT count(*) FROM match_results'))
        finally:
            await source_engine.dispose()
        private_write(state_path, {'vacancy_id':str(vid), 'bindings':[list(row) for row in bindings],
            'source_matches':source_count, 'env':{
                'DATABASE_URL':SOURCE, 'CORE_API_BASE_URL':'http://127.0.0.1:18080/v1',
                'CORE_FEEDBACK_ENABLED':'true','CORE_CONSUMER_KEY':key+'.'+secret,
                'SECRET_KEY':secrets.token_hex(32)}})
        await db.commit()
        print(json.dumps({'copy_import':result,'native_only_fixture':True,'profiles':len(bindings),'production_changed':False}))


async def run():
    try:
        await main()
    finally:
        await engine.dispose()


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(run())
    except Exception as exc:
        print(json.dumps({'prepared':False,'type':type(exc).__name__, 'check':str(exc) if type(exc) is AssertionError else 'private copy operation failed'}))
        raise SystemExit(1) from None
