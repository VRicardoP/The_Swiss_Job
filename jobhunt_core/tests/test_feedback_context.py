"""Read pattern evidence from current state, including negative and unmarked jobs."""
import asyncio

import sqlalchemy as sa

from jobhunt_core.tests.test_integration_api import db, _api, _issue
from jobhunt_core.tests.test_integration_api_feedback import seed, pytestmark


def test_feedback_context_includes_native_rejection_and_unmarked_history(db):
    factory,pid,vid,token = seed(db)
    path = f'/v1/profiles/{pid}'
    assert _api(factory,path+f'/vacancies/{vid}/feedback',token=token,method='PUT',json_body={'feedback':'thumbs_down'}).status_code == 200
    response = _api(factory,path+'/feedback-context',token=token)
    assert response.status_code == 200
    items = response.json()['items']
    assert len(items) == 2
    assert len({row['identity'] for row in items}) == 2
    assert any(row['identity'] == f'vacancy:{vid}' and row['feedback'] in ('thumbs_down','dismissed') for row in items)
    assert any(row['feedback'] is None for row in items)
    assert all(isinstance(row['title'],str) and isinstance(row['tags'],list) for row in items)
    _,_,other = _issue(factory,db[1],'other-context-reader',['matches:read'])
    assert _api(factory,path+'/feedback-context',token=other).status_code == 404


def test_archival_does_not_erase_rejection_context(db):
    factory,pid,vid,token = seed(db)
    path = f'/v1/profiles/{pid}'
    assert _api(factory,path+f'/vacancies/{vid}/feedback',token=token,method='PUT',json_body={'feedback':'dismissed'}).status_code == 200

    async def archive():
        async with factory() as session:
            await session.execute(sa.text('UPDATE vacancies SET archived_at=now() WHERE id=:v'), {'v':vid})
            await session.commit()
    asyncio.run(archive())
    response = _api(factory,path+'/feedback-context',token=token)
    assert response.status_code == 200
    assert any(row['identity'] == f'vacancy:{vid}' and row['feedback']=='dismissed' for row in response.json()['items'])
