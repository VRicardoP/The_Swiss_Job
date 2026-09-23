"""Cutover freeze is early, read-preserving, and covers both legacy tasks."""

import uuid

import pytest

from config import settings
from tasks import search_tasks


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", ""),
        ("PUT", "/{id}"),
        ("DELETE", "/{id}"),
        ("POST", "/{id}/run"),
    ],
)
async def test_freeze_precedes_auth_and_payload_parsing(
    client, monkeypatch, method, path
):
    monkeypatch.setattr(settings, "SAVED_SEARCH_WRITES_FROZEN", True)
    response = await client.request(
        method, "/api/v1/searches" + path.format(id=uuid.uuid4()), content=b""
    )
    assert response.status_code == 503, response.text


async def test_freeze_preserves_reads_and_prevents_task_database_work(
    client, monkeypatch
):
    import database

    monkeypatch.setattr(settings, "SAVED_SEARCH_WRITES_FROZEN", True)
    # The read still reaches auth, unlike mutations.
    assert (await client.get("/api/v1/searches")).status_code == 401

    def forbidden():
        raise AssertionError("frozen executor opened a session")

    monkeypatch.setattr(database, "task_session", forbidden)
    expected = {"status": "disabled", "reason": "write_freeze"}
    assert await search_tasks._run_saved_searches_async() == expected
    assert await search_tasks._run_single_async("unused", "unused") == expected
