import json
import uuid

import httpx
import pytest

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


def test_unknown_destination_fails_but_shadow_has_explicit_fallback():
    fallback = []
    transport = HttpDestinationTransport(
        {"portfolio": "http://bff/inbox"},
        "secret",
        5,
        fallback=lambda destination, event: fallback.append(
            (destination, event["event_id"])
        ),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(204, request=request)
            )
        ),
    )
    event = _event()
    transport("swissjob-shadow", event)
    assert fallback == [("swissjob-shadow", event["event_id"])]
    with pytest.raises(RuntimeError, match="sin inbox HTTP"):
        transport("consumer-sin-config", event)
