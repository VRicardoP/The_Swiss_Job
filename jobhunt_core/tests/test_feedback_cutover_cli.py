"""The offline importer cannot be used to undo a live core writer."""

import asyncio

import httpx
import pytest

from jobhunt_core import feedback_cutover
from jobhunt_core.import_swissjob_feedback import FeedbackMigrationError


@pytest.mark.parametrize(
    "state,allowed",
    [
        ({"writes": "frozen", "writer": "local"}, True),
        ({"writes": "enabled", "writer": "local"}, False),
        ({"writes": "frozen", "writer": "core"}, False),
        ({"writes": "frozen"}, False),
    ],
)
def test_feedback_handover_requires_frozen_old_writer(monkeypatch, state, allowed):
    monkeypatch.setenv("FEEDBACK_FREEZE_URL", "http://bff.test/health/feedback")
    monkeypatch.setenv("FEEDBACK_FREEZE_TOKEN", "private-test-token")
    original = httpx.AsyncClient

    def handler(request):
        assert request.url.path == "/health/feedback"
        assert request.headers["Authorization"] == "Bearer private-test-token"
        return httpx.Response(200, json=state)

    monkeypatch.setattr(
        feedback_cutover.httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
    )
    if allowed:
        asyncio.run(feedback_cutover.require_freeze())
    else:
        with pytest.raises(FeedbackMigrationError, match="must remain frozen"):
            asyncio.run(feedback_cutover.require_freeze())
