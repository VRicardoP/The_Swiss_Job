"""The retained pattern analyzer must consume the authoritative feedback writer."""
import uuid

import httpx
import pytest
from sqlalchemy import select

from config import settings
from models.job_filter import PatternSuggestion
from services.matching import feedback
from services.matching.identity import set_profile_link
from services.pattern_analysis_service import PatternAnalysisService
from services.routing import set_routing
from tests.test_analytics_router import _auth


async def prepare(client, db, monkeypatch, response):
    headers, uid = await _auth(client)
    pid = uuid.uuid4()
    await set_profile_link(db, uid, pid)
    await set_routing(db, 'matching', 'core_primary', profile_id=uid)
    monkeypatch.setattr(settings, 'CORE_FEEDBACK_ENABLED', True)
    monkeypatch.setattr(settings, 'CORE_CONSUMER_KEY', 'test-key')
    calls = []

    def remote(request):
        calls.append(request.url.path)
        assert request.method == 'GET'
        assert request.url.path == f'/v1/profiles/{pid}/feedback-context'
        return response

    monkeypatch.setattr(feedback, 'default_client_factory', lambda: httpx.AsyncClient(
        base_url='http://core.test/v1', transport=httpx.MockTransport(remote)))
    return headers, uid, calls


def context():
    return {'items': [
        {'identity':f'vacancy:{uuid.uuid4()}', 'title':'Senior Nurse', 'company':'Clinic', 'tags':['health'], 'feedback':'thumbs_down'},
        {'identity':f'vacancy:{uuid.uuid4()}', 'title':'Senior Nurse', 'company':'Hospital', 'tags':['health'], 'feedback':'dismissed'},
        {'identity':f'vacancy:{uuid.uuid4()}', 'title':'Primary Teacher', 'company':'School', 'tags':['education'], 'feedback':None},
    ]}


@pytest.mark.asyncio
async def test_core_rejections_are_counted_without_local_jobs(client, db_session, monkeypatch):
    _, uid, calls = await prepare(client, db_session, monkeypatch, httpx.Response(200,json=context()))
    assert await PatternAnalysisService(db_session).get_rejected_count(uid) == 2
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_analyzer_uses_one_authoritative_context(client, db_session, monkeypatch):
    headers, uid, calls = await prepare(client, db_session, monkeypatch, httpx.Response(200,json=context()))
    result = await client.post('/api/v1/analytics/analyze', headers=headers, json={'min_rejected':2})
    assert result.status_code == 200
    assert result.json()['rejected_jobs_analyzed'] == 2
    assert result.json()['suggestions_generated'] > 0
    assert len(calls) == 1
    rows = (await db_session.execute(select(PatternSuggestion).where(PatternSuggestion.user_id == uid))).scalars().all()
    assert any('nurse' in row.pattern for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [httpx.Response(503), httpx.Response(200,json={'items':[{'feedback':'dismissed'}]})])
async def test_unavailable_context_preserves_pending_suggestions(client, db_session, monkeypatch, response):
    headers, uid, _ = await prepare(client, db_session, monkeypatch, response)
    row = PatternSuggestion(user_id=uid, suggestion_type='title_pattern', pattern='keep',
        description='Existing proposal', confidence=0.9, sample_jobs=[], affected_count=2, status='pending')
    db_session.add(row)
    await db_session.commit()
    rid = row.id
    result = await client.post('/api/v1/analytics/analyze', headers=headers, json={'min_rejected':2})
    assert result.status_code == 503
    assert await db_session.get(PatternSuggestion, rid) is not None
