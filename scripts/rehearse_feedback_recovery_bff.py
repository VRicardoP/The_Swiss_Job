"""Copy-only HTTP writes, freeze, process recovery and no-local-fallback checks."""
import asyncio
import hashlib
import json
import logging
import os
import sys
import uuid
from pathlib import Path

ROOT = Path('/evidence')
state = json.loads((ROOT/'feedback-recovery.private.json').read_text())
assert state['env']['DATABASE_URL'] == 'postgresql+asyncpg://swissjob@127.0.0.1:5432/source_copy'
assert state['env']['CORE_API_BASE_URL'] == 'http://127.0.0.1:18080/v1'
os.environ.update(state['env'])

import httpx
from sqlalchemy import text
from config import settings
from core.security import create_access_token
from database import async_session, engine
from main import app


async def source_hash():
    async with async_session() as db:
        values = (await db.execute(text('SELECT to_jsonb(m) FROM match_results m ORDER BY id'))).scalars().all()
        assert len(values) == state['source_matches']
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


async def saved(client, headers, expected):
    response = await client.get('/api/v1/match/saved?limit=200', headers=headers)
    assert response.status_code == 200, 'saved HTTP failure'
    body = response.json()
    entries = body['data']
    assert len(entries) == body['total'], 'saved list unexpectedly exceeds rehearsal page'
    assert any(x['job_hash'] == state['vacancy_id'] for x in entries) == expected, 'native state lost or cross-profile leak'
    return body['total']


async def main():
    mode = sys.argv[1]
    assert mode in {'exercise', 'verify'}
    before = await source_hash()
    vid = state['vacancy_id']
    headers = [{'Authorization':'Bearer '+create_access_token(uuid.UUID(uid))} for uid, _ in state['bindings']]
    assert len(headers) == 2
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://copy-bff') as client:
        path = '/api/v1/match/'+vid
        if mode == 'exercise':
            for body in ({'feedback':'thumbs_down'}, {'feedback':'thumbs_up'}):
                r = await client.post(path+'/feedback', headers=headers[0], json=body)
                assert r.status_code == 200, 'native write failed'
            r = await client.delete(path+'/feedback', headers=headers[0])
            assert r.status_code == 200, 'clear failed'
            r = await client.post(path+'/feedback', headers=headers[0], json={'feedback':'thumbs_up'})
            assert r.status_code == 200
            r = await client.post(path+'/implicit', headers=headers[0], json={'action':'opened'})
            assert r.status_code == 200
            r = await client.post(path+'/feedback', headers=headers[1], json={'feedback':'thumbs_down'})
            assert r.status_code == 200
        totals = [await saved(client, h, i == 0) for i, h in enumerate(headers)]
        settings.FEEDBACK_WRITES_FROZEN = True
        for method, suffix, body in [('POST','feedback',{'feedback':'thumbs_up'}), ('DELETE','feedback',None), ('POST','implicit',{'action':'opened'})]:
            r = await client.request(method, path+'/'+suffix, json=body)
            assert r.status_code == 503, 'freeze did not precede auth'
        assert (await client.get('/health/feedback')).json() == {'writes':'frozen','writer':'core'}
        assert await saved(client, headers[0], True) == totals[0]
        settings.FEEDBACK_WRITES_FROZEN = False
        if mode == 'verify':
            settings.CORE_API_BASE_URL = 'http://127.0.0.1:18081/v1'
            r = await client.post(path+'/feedback', headers=headers[0], json={'feedback':'dismissed'})
            assert r.status_code == 503, 'core failure did not fail closed'
            settings.CORE_API_BASE_URL = state['env']['CORE_API_BASE_URL']
            assert await saved(client, headers[0], True) == totals[0]
    assert await source_hash() == before, 'local writer changed'
    print(json.dumps({'phase':mode, 'saved_native_only_survives':True, 'separate_users':True,
        'freeze_before_auth':True, 'frozen_reads_live':True, 'local_matches_unchanged':True,
        'core_failure_closed':mode=='verify', 'saved_totals':totals, 'production_changed':False}))


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
        print(json.dumps({'verified':False,'type':type(exc).__name__, 'check':str(exc) if type(exc) is AssertionError else 'copy-only operation failed'}))
        raise SystemExit(1) from None
