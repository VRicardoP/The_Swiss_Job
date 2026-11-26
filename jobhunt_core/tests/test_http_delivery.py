import json
import uuid

import httpx
import pytest

from jobhunt_core.delivery import PermanentDeliveryError
from jobhunt_core.http_delivery import HttpDestinationTransport


def _event():
    return {
        "event_id": uuid.uuid4(),
        "type": "match.evaluated",
        "aggregate_id": "eval-key",
        "payload": {"profile_id": str(uuid.uuid4())},
    }


def test_http_transport_sends_authenticated_idempotent_envelope():
    seen = {}

    def handler(request):
        seen["request"] = request
        return httpx.Response(204, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    event = _event()
    transport = HttpDestinationTransport(
        {"portfolio": "http://bff/api/v1/integration/inbox"},
        "shared-secret",
        5,
        client=client,
    )
    transport("portfolio", event)
    request = seen["request"]
    assert request.headers["authorization"] == "Bearer shared-secret"
    assert request.headers["x-event-id"] == str(event["event_id"])
    body = json.loads(request.content)
    assert body["consumer_id"] == "portfolio"
    assert body["event"]["event_id"] == str(event["event_id"])


def test_http_transport_propagates_non_success_for_retry():
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, request=request)
        )
    )
    transport = HttpDestinationTransport(
        {"portfolio": "http://bff/inbox"}, "secret", 5, client=client
    )
    with pytest.raises(httpx.HTTPStatusError):
        transport("portfolio", _event())


def test_unknown_destination_fails_closed():
    transport = HttpDestinationTransport(
        {"portfolio": "http://bff/inbox"},
        "secret",
        5,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(204, request=request)
            )
        ),
    )
    event = _event()
    with pytest.raises(RuntimeError, match="sin inbox HTTP"):
        transport("swissjob-shadow", event)


def _answering(status):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, request=request)
        )
    )
    return HttpDestinationTransport(
        {"portfolio": "http://bff/inbox"}, "secret", 5, client=client
    )


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422])
def test_permanent_4xx_is_flagged_for_immediate_dead_letter(status):
    """D6: a 4xx that is not 408 or 429 answers the same on every retry."""
    with pytest.raises(PermanentDeliveryError, match=f"HTTP {status}"):
        _answering(status)("portfolio", _event())


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503])
def test_transient_status_keeps_its_retries(status):
    with pytest.raises(httpx.HTTPStatusError) as raised:
        _answering(status)("portfolio", _event())
    assert not isinstance(raised.value, PermanentDeliveryError)
