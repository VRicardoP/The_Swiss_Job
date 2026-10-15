"""School cutover contracts, including the old local writer as a negative control."""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select, update

from config import settings
from models.job import Job
from models.match_result import MatchResult
from services.matching.identity import set_profile_link
from services.schools import CoreSchools, CoreUnavailableError, resolve_schools
from services.schools.http_client import SchoolClient
from services.schools.presentation import (
    clear_school_presentation_cache,
    overlay_school_results,
)
from tests.test_applications_contract import seed_job, seed_match, JOB_HASH


async def _register(client):
    email = f"schools-{uuid.uuid4().hex[:8]}@example.com"
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "TestPass123!", "gdpr_consent": True},
    )
    assert response.status_code == 201
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    return uuid.UUID(me.json()["id"]), headers


async def test_native_school_vacancy_keeps_actionable_identity(
    client, db_session, monkeypatch
):
    from services.matching.core_client import CoreMatching
    from tests.test_matching_contract import _match_dto

    uid, _ = await _register(client)
    await seed_job(db_session)
    await seed_match(db_session, uid)
    await db_session.commit()
    pid = uuid.uuid4()
    await set_profile_link(db_session, uid, pid)
    item = _match_dto("python_zurich", legacy=False)
    item["vacancy"]["primary_listing"]["source"] = "school-observation"
    rows = [
        {
            "vacancy_id": item["vacancy"]["id"],
            "source_ref": JOB_HASH,
            "metadata": {"source": "swiss_schools_zis"},
        }
    ]
    pages = AsyncMock(return_value=rows)
    monkeypatch.setattr(SchoolClient, "pages", pages)
    matcher = CoreMatching(db_session, client_factory=lambda: None)
    # Devuelve (items, total): el total lo informa el core desde el punto 5,
    # y sin el el consumidor vuelve a contar recorriendo el feed.
    monkeypatch.setattr(
        matcher, "_fetch_full_feed", AsyncMock(return_value=([item], 1))
    )
    results, total = await matcher.results(uid)
    assert total == 1
    assert results[0]["match"].job_hash == JOB_HASH
    assert results[0]["job"].source == "swiss_schools_zis"
    pages.assert_awaited_once_with("/school-jobs")
    cached, cached_total = await matcher.results(uid)
    assert cached_total == 1 and cached[0]["match"].job_hash == JOB_HASH
    pages.assert_awaited_once_with("/school-jobs")
    # A corpus change invalidates the bounded presentation cache.
    clear_school_presentation_cache()
    pages.return_value = []
    assert await matcher.results(uid) == ([], 0)


class SchoolApi:
    def __init__(self, pid):
        self.pid = pid
        self.mid = uuid.uuid4()
        self.calls = []
        self.items = []
        self.enabled = False

    def handle(self, request):
        self.calls.append((request.method, request.url.path))
        path = request.url.path
        stamp = datetime.now(timezone.utc).isoformat()
        if path == "/v1/schools":
            body = {
                "items": [
                    {
                        "id": str(self.mid),
                        "school_id": str(uuid.uuid4()),
                        "external_ref": "zis_zurich",
                        "version": 1,
                        "settings": {
                            "name": "Core School",
                            "city": "Core City",
                            "policy": "portal_only",
                            "group_tier": "A",
                            "template_letter": "B",
                        },
                    }
                ],
                "next_cursor": None,
            }
        elif path.endswith("school-preferences"):
            if request.method == "PUT":
                self.enabled = json.loads(request.content)["enabled"]
            body = {"enabled": self.enabled}
        elif path.endswith("school-applications"):
            if request.method == "GET":
                ref = request.url.params.get("source_ref")
                body = {
                    "items": [
                        r for r in self.items if ref is None or r["source_ref"] == ref
                    ],
                    "next_cursor": None,
                }
            else:
                data = json.loads(request.content)
                body = {
                    "id": str(uuid.uuid4()),
                    "profile_id": str(self.pid),
                    "draft_content": None,
                    "status": "detected",
                    "context": {},
                    "created_at": stamp,
                    "updated_at": stamp,
                    "version": 1,
                    **data,
                }
                self.items.append(body)
        else:
            body = next(r for r in self.items if r["id"] == path.rsplit("/", 1)[-1])
            if request.method == "PATCH":
                body.update(json.loads(request.content))
        return httpx.Response(200, json=body, headers={"ETag": '"school-test"'})

    def install(self, monkeypatch):
        from services.schools import http_client

        monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "isolated-test-only")
        monkeypatch.setattr(
            http_client,
            "default_client_factory",
            lambda: httpx.AsyncClient(
                base_url="https://core.test/v1",
                transport=httpx.MockTransport(self.handle),
            ),
        )


@pytest.mark.parametrize("mode", ["core_primary", "rollback_pending"])
async def test_core_school_modes_are_real_and_never_fallback(
    client, db_session, monkeypatch, mode
):
    uid, headers = await _register(client)
    assert isinstance(await resolve_schools(db_session, uid), CoreSchools)
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "")
    assert (
        await client.get("/api/v1/watchlist/schools", headers=headers)
    ).status_code == 503
    api = SchoolApi(uuid.uuid4())
    api.install(monkeypatch)
    response = await client.get("/api/v1/watchlist/schools", headers=headers)
    assert response.status_code == 200
    assert [s["name"] for s in response.json()["schools"]] == ["Core School"]


async def test_watchlist_writes_only_core_and_uses_core_calendar_metadata(
    client, db_session, monkeypatch
):
    uid, headers = await _register(client)
    await seed_job(db_session)
    await seed_match(
        db_session, uid, application_status="reviewed", draft_letter="OLD LOCAL"
    )
    await db_session.flush()
    await db_session.execute(
        update(Job).where(Job.hash == JOB_HASH).values(tags=["zis_zurich"])
    )
    await db_session.commit()
    pid = uuid.uuid4()
    await set_profile_link(db_session, uid, pid)
    api = SchoolApi(pid)
    api.install(monkeypatch)
    base = f"/api/v1/watchlist/match/{JOB_HASH}"
    response = await client.post(
        base + "/status", headers=headers, json={"application_status": "sent"}
    )
    assert response.status_code == 200, response.text
    assert api.items[0]["status"] == "sent"
    local = await db_session.scalar(
        select(MatchResult).where(MatchResult.user_id == uid)
    )
    await db_session.refresh(local)
    assert (local.application_status, local.draft_letter) == ("reviewed", "OLD LOCAL")
    assert (await client.get(base + "/draft", headers=headers)).status_code == 404
    calendar = await client.get(base + "/calendar.ics", headers=headers)
    assert calendar.status_code == 200 and "Core School" in calendar.text


async def test_school_freeze_precedes_auth_and_llm(client, monkeypatch):
    monkeypatch.setattr(settings, "SCHOOL_WRITES_FROZEN", True)
    for suffix, body in [("status", {"application_status": "sent"}), ("draft", {})]:
        response = await client.post(f"/api/v1/watchlist/match/any/{suffix}", json=body)
        assert response.status_code == 503
    assert (await client.get("/api/v1/watchlist/schools")).status_code == 401


@pytest.mark.parametrize("body", [None, [], {"items": None}, {"items": [None]}])
async def test_school_client_rejects_malformed_success(body):
    client = SchoolClient(
        lambda: httpx.AsyncClient(
            base_url="https://core.test/v1",
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=body, headers={"etag": '"x"'})
            ),
        )
    )
    with pytest.raises(CoreUnavailableError):
        await client.monitors()


async def test_core_state_ownership_is_checked():
    api = SchoolApi(uuid.uuid4())
    api.items.append(
        {
            "id": str(uuid.uuid4()),
            "profile_id": str(uuid.uuid4()),
            "monitor_id": str(api.mid),
            "source_ref": "job",
            "status": "sent",
            "draft_content": "private",
            "context": {},
            "version": 1,
            "created_at": "2026-09-13T00:00:00Z",
            "updated_at": "2026-09-13T00:00:00Z",
        }
    )
    client = SchoolClient(
        lambda: httpx.AsyncClient(
            base_url="https://core.test/v1", transport=httpx.MockTransport(api.handle)
        )
    )
    with pytest.raises(CoreUnavailableError, match="ownership"):
        await client.states(api.pid)


async def test_feed_overlay_does_not_dirty_local_match(client, db_session, monkeypatch):
    uid, _ = await _register(client)
    await seed_job(db_session)
    await seed_match(db_session, uid, application_status="sent", draft_letter="LOCAL")
    await db_session.flush()
    await db_session.execute(
        update(Job).where(Job.hash == JOB_HASH).values(tags=["zis_zurich"])
    )
    await db_session.commit()
    pid = uuid.uuid4()
    await set_profile_link(db_session, uid, pid)
    api = SchoolApi(pid)
    api.install(monkeypatch)
    job = await db_session.scalar(select(Job).where(Job.hash == JOB_HASH))
    match = await db_session.scalar(
        select(MatchResult).where(MatchResult.user_id == uid)
    )
    result = await overlay_school_results(
        db_session, uid, [{"match": match, "job": job}]
    )
    assert result[0]["match"].application_status == "detected"
    assert result[0]["match"].draft_letter is None
    assert result[0]["school"].name == "Core School"
    calls = list(api.calls)
    again = await overlay_school_results(
        db_session, uid, [{"match": match, "job": job}]
    )
    assert again[0]["school"].name == "Core School"
    assert api.calls == calls
    assert match.application_status == "sent" and match.draft_letter == "LOCAL"
    assert match not in db_session.dirty
