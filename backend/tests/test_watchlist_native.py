"""A native observation is actionable without any legacy Job or MatchResult."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from config import settings
from services.schools.http_client import SchoolClient
from services.schools.watchlist_state import CoreWatchlist
from services.schools.port import CoreUnavailableError
from tests.test_applications_contract import seeded


@pytest.mark.asyncio
async def test_native_school_can_create_draft_without_local_corpus(seeded, db_session, monkeypatch):
    user_id, _, _ = seeded
    monkeypatch.setattr(settings, "CORE_FEEDBACK_ENABLED", True)
    mid, jid = uuid.uuid4(), uuid.uuid4()
    ref = "f" * 32
    client = SchoolClient()
    client.states = AsyncMock(return_value=[])
    client.monitors = AsyncMock(return_value=[SimpleNamespace(
        id=mid, external_ref="school-example", settings={"name": "Example School"}
    )])
    client.request = AsyncMock(return_value=httpx.Response(200, json={
        "items": [{"id": str(jid), "monitor_id": str(mid), "source_ref": ref,
                   "created_at": "2026-09-19T10:00:00Z", "metadata": {
                       "title": "IT Technician", "url": "https://school.example/job/1",
                   }}], "next_cursor": None,
    }))
    client.write_state = AsyncMock()
    service = CoreWatchlist(db_session, client)
    pair = await service.get_match(user_id, ref)
    assert pair[0].application_status == "detected"
    assert pair[1].title == "IT Technician"
    assert await service.save_draft(user_id, ref, "Draft, not an email") is True
    client.write_state.assert_awaited_once()
    call = client.write_state.await_args
    assert call.kwargs["school_job_id"] == jid
    assert call.args[3] == {"draft_content": "Draft, not an email"}
    assert call.kwargs["context"]["job_url"] == "https://school.example/job/1"
    client.request.assert_awaited_with("GET", "/school-jobs", params={"dedup_key": ref, "limit": 2})


@pytest.mark.asyncio
async def test_native_school_lookup_refuses_ambiguous_identity(seeded, db_session, monkeypatch):
    user_id, _, _ = seeded
    monkeypatch.setattr(settings, "CORE_FEEDBACK_ENABLED", True)
    client = SchoolClient()
    client.states = AsyncMock(return_value=[])
    client.request = AsyncMock(return_value=httpx.Response(200, json={"items": [{}, {}], "next_cursor": None}))
    client.write_state = AsyncMock()
    with pytest.raises(CoreUnavailableError):
        await CoreWatchlist(db_session, client).save_draft(user_id, "f" * 32, "private")
    client.write_state.assert_not_called()
