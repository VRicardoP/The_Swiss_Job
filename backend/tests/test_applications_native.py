"""Native UUIDs must remain actionable without a legacy jobs row."""

import json
import uuid

import httpx
import pytest

from schemas.applications import ApplicationCreate
from services.applications import ApplicationJobNotFoundError, CoreUnavailableError
from tests.test_applications_contract import make_core, seeded  # noqa: F401


def test_application_request_accepts_native_identity():
    vid = str(uuid.uuid4())
    assert ApplicationCreate(job_hash=vid).job_hash == vid


@pytest.mark.asyncio
async def test_native_application_create_and_list_preserve_identity(seeded, db_session):  # noqa: F811  (la fixture, no una redefinición)
    user_id, fake, _ = seeded
    vid = str(uuid.uuid4())
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == f"/v1/vacancies/{vid}":
            return httpx.Response(
                200,
                json={
                    "id": vid,
                    "title": "Native position",
                    "company": "Native company",
                    "description": "Native description",
                    "location": "Bern",
                    "tags": [],
                    "primary_listing": {
                        "source": "remotive",
                        "url": "https://example.test/native",
                    },
                    "listings": [],
                },
            )
        response = fake.handler(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["vacancy_id"] == vid
            assert body["title"] == "Native position"
            # The real /v1 DTO includes the canonical vacancy identity.
            item = response.json()
            item["vacancy_id"] = vid
            fake.items[item["id"]]["vacancy_id"] = vid
            return httpx.Response(201, json=item)
        return response

    core = make_core(db_session, httpx.MockTransport(handler))
    created = await core.create(user_id, vid, notes="My note")
    assert created.job_hash == vid
    assert created.job_title == "Native position"
    listing = await core.list(user_id)
    assert listing.total == 1 and listing.data[0].job_hash == vid
    assert [r.method for r in requests] == ["GET", "POST", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error", [(404, ApplicationJobNotFoundError), (503, CoreUnavailableError)]
)
async def test_native_lookup_failure_does_not_create(seeded, db_session, status, error):  # noqa: F811  (la fixture, no una redefinición)
    user_id, _, _ = seeded
    seen = []

    def handler(request):
        seen.append(request.method)
        return httpx.Response(status)

    core = make_core(db_session, httpx.MockTransport(handler))
    with pytest.raises(error):
        await core.create(user_id, str(uuid.uuid4()))
    assert seen == ["GET"]
