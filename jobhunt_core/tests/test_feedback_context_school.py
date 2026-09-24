"""School evidence remains one logical job before/after corpus linkage."""
import asyncio
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core.tests import test_integration_api as api
from jobhunt_core.tests.test_integration_api import db, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.test_integration_school_jobs import school_db, _observation  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
from jobhunt_core.tests.test_integration_api_schools import _create, _request, SCOPES


@pytest.mark.parametrize('school_feedback', ['thumbs_down', None])
def test_context_coalesces_linked_school_latest_intent(school_db, school_feedback):  # noqa: F811  (la fixture, no una redefinición)
    factory, made = school_db
    pid, vacancies, _ = api._seed_matches(factory, made)
    _, _, token = api._issue(factory, made, 'tenant-match', [*SCOPES, 'matches:read', 'applications:write'])
    vid = next(iter(vacancies.values()))
    monitor = _create(factory, token).json()
    job = _request(factory, token, f"/v1/schools/{monitor['id']}/jobs", 'POST',
                   _observation(publish_missing=False), 'observe').json()['item']
    path = f'/v1/profiles/{pid}'
    assert _request(factory, token, path+f'/vacancies/{vid}/feedback', 'PUT', {'feedback':'thumbs_up'}, 'canonical').status_code == 200
    assert _request(factory, token, path+f"/school-jobs/{job['id']}/feedback", 'PUT', {'feedback':school_feedback}, 'school').status_code == 200
    before = _request(factory, token, path+'/feedback-context')
    assert before.status_code == 200, before.text
    items = before.json()['items']
    assert len(items) == 3
    observation = next(row for row in items if row['identity'] == f"school:{job['id']}")
    assert observation['title'] == 'IT Technician'
    assert observation['feedback'] == school_feedback
    assert observation['tags'] == [monitor['external_ref']]

    async def link():
        async with factory() as session:
            await session.execute(sa.text('UPDATE school_job_details SET vacancy_id=:v,quarantine_reason=NULL WHERE id=:j'), {'v':vid, 'j':uuid.UUID(job['id'])})
            await session.commit()
    asyncio.run(link())
    after = _request(factory, token, path+'/feedback-context')
    assert after.status_code == 200, after.text
    items = after.json()['items']
    assert len(items) == 2
    canonical = next(row for row in items if row['identity'] == f'vacancy:{vid}')
    assert canonical['feedback'] == school_feedback


def test_context_never_reports_truncated_collection_as_complete(db, monkeypatch):  # noqa: F811  (la fixture, no una redefinición)
    from jobhunt_core.api import v1_feedback
    from jobhunt_core.tests.test_integration_api_feedback import seed
    factory, pid, _, token = seed(db)
    monkeypatch.setattr(v1_feedback, 'MAX_FEEDBACK_CONTEXT', 1)
    response = api._api(factory, f'/v1/profiles/{pid}/feedback-context', token=token)
    assert response.status_code == 503


def test_feedback_context_requires_read_scope(db):  # noqa: F811  (la fixture, no una redefinición)
    from jobhunt_core.tests.test_integration_api_feedback import seed
    factory, pid, _, token = seed(db, scopes=('applications:write',))
    assert api._api(factory, f'/v1/profiles/{pid}/feedback-context', token=token).status_code == 403
