"""TheHub's slim listing requires detail; failed detail cannot erase good text."""

import asyncio
import json

import httpx
import pytest


def item(i=1):
    return {
        "id": f"{i:024x}",
        "title": "English teacher",
        "company": {"name": "School"},
        "isRemote": True,
    }


def detail(i=1):
    return {
        **item(i),
        "description": "<p>English TEFL</p>",
        "location": {"address": "Remote"},
        "createdAt": "2026-09-19T12:00:00Z",
    }


def fetch(pages, details=None, monkeypatch=None):
    from jobhunt_core.harvest.providers import native_thehub as module

    if monkeypatch:
        monkeypatch.setattr(module, "PAUSE_S", 0)
    requests = []

    async def run():
        def transport(request):
            requests.append(request)
            if request.url.path == "/v2/jobs":
                value = pages[int(request.url.params["page"]) - 1]
            else:
                key = request.url.path.rsplit("/", 1)[1]
                value = (details or {}).get(key, detail(int(key, 16)))
            return (
                httpx.Response(value)
                if type(value) is int
                else httpx.Response(200, content=json.dumps(value).encode())
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            return await module.TheHubProvider().fetch_new({}, None, client), requests

    return asyncio.run(run())


def test_detail_is_preserved_and_normalized(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, requests = fetch([{"docs": [item()], "pages": 1}], monkeypatch=monkeypatch)
    assert result.complete and len(requests) == 2
    row = result.listings[0]
    assert (
        row.payload == detail() and row.url == "https://thehub.io/jobs/" + item()["id"]
    )
    content = normalize_offer("thehub", row.payload)
    assert content["description"] == "English TEFL" and content["location"] == "Remote"


@pytest.mark.parametrize(
    "broken",
    [503, None, [], {}, {"id": "000000000000000000000099", "description": "WRONG"}],
)
def test_failed_detail_is_not_a_destructive_sparse_refresh(broken, monkeypatch):
    result, _ = fetch(
        [{"docs": [item(), item(2)], "pages": 1}], {item()["id"]: broken}, monkeypatch
    )
    assert not result.complete and result.error
    assert (
        len(result.listings) == 1 and result.listings[0].payload["id"] == item(2)["id"]
    )


@pytest.mark.parametrize(
    "body", [None, [], {}, {"docs": "bad", "pages": 1}, {"docs": [], "pages": 2}]
)
def test_malformed_listing_not_empty(body, monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch([body], monkeypatch=monkeypatch)


def test_invalid_object_id_never_requests_detail(monkeypatch):
    result, requests = fetch(
        [{"docs": [item(), {**item(2), "id": "../../admin"}], "pages": 1}],
        monkeypatch=monkeypatch,
    )
    assert len(requests) == 2 and len(result.listings) == 1 and not result.complete


def test_final_page_and_empty_contract(monkeypatch):
    result, _ = fetch([{"docs": [], "pages": 0}], monkeypatch=monkeypatch)
    assert result.complete
    result, requests = fetch(
        [{"docs": [item()], "pages": 2}, {"docs": [item(2)], "pages": 2}],
        monkeypatch=monkeypatch,
    )
    assert result.complete and len(result.listings) == 2 and len(requests) == 4


def test_late_listing_failure_keeps_completed_detail(monkeypatch):
    result, _ = fetch([{"docs": [item()], "pages": 2}, 503], monkeypatch=monkeypatch)
    assert not result.complete and result.error and len(result.listings) == 1
