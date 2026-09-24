"""Feedback cutover preserves quarantined school favourites and their identity."""

import json
import uuid

import httpx
import pytest

from tests.test_applications_contract import seeded  # noqa: F401  (fixture de pytest: se importa para que la resuelva por nombre)
from tests.test_core_feedback import writer
from services.matching.port import CoreUnavailableError


@pytest.mark.asyncio
async def test_school_without_canonical_vacancy_can_be_saved_cleared_and_viewed(
    seeded,  # noqa: F811  (la fixture, no una redefinición)
    db_session,
):
    user_id, fake, _ = seeded
    jid, ref = str(uuid.uuid4()), "b" * 32
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/school-jobs":
            return httpx.Response(
                200,
                json={
                    "items": [{"id": jid, "source_ref": ref, "vacancy_id": None}],
                    "next_cursor": None,
                },
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "items": [
                        {
                            "vacancy_id": None,
                            "school_job_id": jid,
                            "job_ref": ref,
                            "feedback": "thumbs_up",
                            "updated_at": "2026-09-19T10:00:00Z",
                            "score_final": 0,
                            "scores": {},
                            "explanation": None,
                            "content": {
                                "title": "IT Teacher",
                                "company": "School",
                                "tags": ["school-one"],
                            },
                            "url": "https://school.example/jobs/1",
                            "source": "school-one",
                        }
                    ],
                },
            )
        assert f"/school-jobs/{jid}/" in request.url.path
        return httpx.Response(
            200,
            json={
                "profile_id": fake.profile_id,
                "school_job_id": jid,
                **json.loads(request.content),
            },
        )

    service = writer(db_session, handler)
    assert await service.submit_feedback(user_id, ref, "thumbs_up")
    rows, total = await service.saved(user_id)
    assert total == 1 and rows[0]["match"].job_hash == ref
    assert rows[0]["job"].title == "IT Teacher"
    assert rows[0]["job"].tags == ["school-one"]
    assert await service.clear_feedback(user_id, ref)
    assert await service.record_implicit_feedback(user_id, ref, "opened")
    assert not any(
        "/vacancies/" in str(r.url) or "/listing-references" in str(r.url)
        for r in requests
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page",
    [
        {
            "items": [{"id": str(uuid.uuid4()), "source_ref": "wrong"}],
            "next_cursor": None,
        },
        {"items": [], "next_cursor": "more"},
        {
            "items": [{"id": str(uuid.uuid4())}, {"id": str(uuid.uuid4())}],
            "next_cursor": None,
        },
    ],
)
async def test_ambiguous_school_reference_never_falls_back_or_writes(
    seeded,  # noqa: F811  (la fixture, no una redefinición)
    db_session,
    page,
):
    user_id, _, _ = seeded
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=page)

    with pytest.raises(CoreUnavailableError):
        await writer(db_session, handler).submit_feedback(
            user_id, "c" * 32, "thumbs_up"
        )
    assert len(requests) == 1 and requests[0].method == "GET"
