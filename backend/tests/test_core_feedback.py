"""F writer: native UUID and legacy aliases, without local match mutations."""

import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from models.match_result import MatchResult
from services.matching.feedback import CoreFeedback
from services.matching.port import CoreUnavailableError
from tests.test_applications_contract import seeded  # noqa: F401


def writer(db, handler):
    return CoreFeedback(db, client_factory=lambda: httpx.AsyncClient(
        base_url="http://core.test/v1", transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_feedback_all_writes_without_local_match(seeded, db_session, legacy):  # noqa: F811  (la fixture, no una redefinición)
    user_id, fake, _ = seeded
    vid = str(uuid.uuid4())
    ref = "a" * 32 if legacy else vid
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/school-jobs":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if request.url.path == "/v1/listing-references":
            assert request.url.params["external_id"] == ref
            return httpx.Response(200, json={"vacancy_id": vid})
        assert f"/profiles/{fake.profile_id}/vacancies/{vid}/" in request.url.path
        data = json.loads(request.content)
        return httpx.Response(200, json={"profile_id": fake.profile_id, "vacancy_id": vid, **data})

    core = writer(db_session, handler)
    assert await core.submit_feedback(user_id, ref, "thumbs_down")
    assert await core.clear_feedback(user_id, ref)
    assert await core.record_implicit_feedback(user_id, ref, "view_time", 500)
    writes = [r for r in requests if r.method != "GET"]
    assert len({r.headers["idempotency-key"] for r in writes}) == 3
    assert [r.method for r in writes] == ["PUT", "PUT", "POST"]
    assert await db_session.scalar(select(func.count()).select_from(MatchResult)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(200, json=[]),
                                     httpx.Response(200, json={"profile_id": "wrong"})])
async def test_feedback_fails_closed_on_core_error(seeded, db_session, response):  # noqa: F811  (la fixture, no una redefinición)
    user_id, _, _ = seeded
    with pytest.raises(CoreUnavailableError):
        await writer(db_session, lambda _: response).submit_feedback(user_id, str(uuid.uuid4()), "thumbs_up")
    assert await db_session.scalar(select(func.count()).select_from(MatchResult)) == 0


@pytest.mark.asyncio
async def test_feedback_saved_historical_page_uses_uuid_and_server_pagination(seeded, db_session):  # noqa: F811  (la fixture, no una redefinición)
    user_id, _, _ = seeded
    vid = str(uuid.uuid4())

    def handler(request):
        assert dict(request.url.params) == {"limit": "1", "offset": "5"}
        return httpx.Response(200, json={"total": 7, "items": [{
            "vacancy_id": vid, "feedback": "thumbs_up", "updated_at": "2026-09-19T10:00:00Z",
            "score_final": 0, "scores": {}, "explanation": None,
            "content": {"title": "Archived native job", "company": "Example", "tags": []},
            "url": "https://example.test/archived", "source": "legacy:arbeitnow",
        }]})
    items, total = await writer(db_session, handler).saved(user_id, limit=1, offset=5)
    assert total == 7 and len(items) == 1
    assert items[0]["match"].job_hash == vid and items[0]["match"].feedback == "thumbs_up"
    assert items[0]["job"].source == "arbeitnow"
