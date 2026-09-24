"""NAV scopes are the two real remote facets, with independent bounded sweeps."""

import asyncio
import json

import httpx
import pytest


def hit(i=1):
    return {
        "_id": f"00000000-0000-4000-8000-{i:012d}",
        "_source": {
            "title": "English teacher",
            "businessName": "School",
            "locationList": [{"city": "Oslo", "county": "OSLO", "country": "Norway"}],
            "generatedSearchMetadata": {"shortSummary": "<p>TEFL teacher</p>"},
            "properties": {"searchtagsai": ["Education", "education"]},
            "published": "2026-09-19T12:00:00Z",
        },
    }


def fetch(pages, params=None, monkeypatch=None):
    from jobhunt_core.harvest.providers import native_nav as module

    if monkeypatch:
        monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    requests = []

    async def run():
        def transport(request):
            requests.append(request)
            value = pages[len(requests) - 1]
            return (
                httpx.Response(value)
                if type(value) is int
                else httpx.Response(200, content=json.dumps(value).encode())
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await module.NavProvider().fetch_new(
                params or {"remote": "Kun hjemmekontor"}, None, client
            )
        return result, requests

    return asyncio.run(run())


def page(items, total=None):
    return {
        "hits": {
            "hits": items,
            "total": {
                "value": len(items) if total is None else total,
                "relation": "eq",
            },
        }
    }


def test_native_fields_raw_and_scope():
    from jobhunt_core.harvest.normalize import normalize_offer

    original = hit()
    result, requests = fetch([page([original])])
    assert result.complete and result.listings[0].payload == original
    row = result.listings[0]
    assert row.url.endswith(original["_id"])
    assert requests[0].url.params["remote"] == "Kun hjemmekontor"
    content = normalize_offer("nav_arbeidsplassen", row.payload)
    assert content["location"] == "Oslo, Norway"
    assert content["description"] == "TEFL teacher"
    assert content["tags"] == ["Education", "english", "tefl"]


@pytest.mark.parametrize(
    "body", [None, [], {}, {"hits": []}, {"hits": {}}, {"hits": {"hits": "bad"}}]
)
def test_bad_envelope_not_empty(body):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch([body])


@pytest.mark.parametrize(
    "total", [None, True, -1, "5", {"value": True}, {"value": 4, "relation": "other"}]
)
def test_bad_total_not_complete(total):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch([{"hits": {"hits": [hit()], "total": total}}])


def test_short_page_with_more_total_continues_and_late_error_preserves(monkeypatch):
    result, requests = fetch([page([hit()], 101), 503], monkeypatch=monkeypatch)
    assert not result.complete and result.error and len(result.listings) == 1
    assert len(requests) == 2
    assert requests[1].url.params["from"] == "1"


def test_invalid_neighbor_is_partial():
    result, _ = fetch([page([hit(), {"_id": "../../bad", "_source": {}}])])
    assert not result.complete and len(result.listings) == 1 and result.error


def test_empty_facet_and_invalid_remote():
    from jobhunt_core.harvest.provider import ProviderConfigError

    assert fetch([page([])])[0].complete
    with pytest.raises(ProviderConfigError):
        fetch([], {"remote": "Ingen mulighet for hjemmekontor"})


def test_lower_bound_total_requires_empty_page(monkeypatch):
    first = page([hit()], 1)
    first["hits"]["total"]["relation"] = "gte"
    result, requests = fetch([first, page([])], monkeypatch=monkeypatch)
    assert result.complete and len(requests) == 2


def test_es6_integer_total():
    body = page([hit()])
    body["hits"]["total"] = 1
    assert fetch([body])[0].complete


def test_rate_limit_preserves_prefix_without_retry(monkeypatch):
    result, requests = fetch([page([hit()], 200), 429], monkeypatch=monkeypatch)
    assert len(requests) == 2
    assert not result.complete and result.error == "http_429"
    assert [row.external_id for row in result.listings] == [hit()["_id"]]


def test_pacing_uses_remaining_sweep_budget(monkeypatch):
    from jobhunt_core.harvest.providers import native_nav as module

    clock = [0.0]
    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    monkeypatch.setattr(module, "SWEEP_BUDGET_S", 15)
    # Do not patch PAGE_PAUSE_S: this asserts the production pacing value.
    result, requests = fetch([page([hit()], 300), page([hit(2)], 300)])
    assert sleeps == [10, 5]
    assert len(requests) == 2 and result.pages_fetched == 2
    assert not result.complete and len(result.listings) == 2
