"""Reject invalid whole-history responses rather than deriving misleading rates."""

import httpx
import pytest

from services.matching.feedback import CoreFeedback
from services.matching.port import CoreUnavailableError
from tests.test_pattern_core_authority import context, prepare


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["duplicate", "identity", "feedback"])
async def test_invalid_context_fails_closed(client, db_session, monkeypatch, fault):
    body = context()
    if fault == "duplicate":
        body["items"].append(body["items"][0])
    elif fault == "identity":
        body["items"][0]["identity"] = "unvalidated-reference"
    else:
        body["items"][0]["feedback"] = "unknown-value"
    _, uid, calls = await prepare(
        client, db_session, monkeypatch, httpx.Response(200, json=body)
    )
    with pytest.raises(CoreUnavailableError):
        await CoreFeedback(db_session).context(uid)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_whole_context_budget_does_not_extend_interactive_writes(
    client, db_session, monkeypatch
):
    _, uid, _ = await prepare(
        client, db_session, monkeypatch, httpx.Response(200, json=context())
    )
    budgets = []

    def response(request):
        budgets.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json=context())

    feedback = CoreFeedback(
        db_session,
        client_factory=lambda: httpx.AsyncClient(
            base_url="http://core.test/v1",
            transport=httpx.MockTransport(response),
            timeout=5,
        ),
    )
    await feedback.context(uid)
    # The transport request helper is shared by interactive reads/writes.
    await feedback._request("PUT", "/synthetic", json={})
    assert budgets == [45.0, 5]
