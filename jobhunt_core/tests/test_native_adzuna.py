"""Adzuna preserves ReactPortfolio's verified six-request search contract."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest


def row(identity="ad-1", **changes):
    value = {
        "id": identity,
        "title": "<b>Python Developer</b>",
        "company": {"display_name": "Acme"},
        "category": {"label": "IT Jobs"},
        "location": {
            "area": ["United States", "New York"],
            "display_name": "New York, NY",
        },
        "description": "<p>Build React APIs with Docker.</p>",
        "redirect_url": "https://www.adzuna.com/details/ad-1",
        "created": "2026-10-12T10:00:00Z",
        "salary_min": 80000.5,
        "salary_max": 120000,
        "contract_type": "permanent",
        "contract_time": "full_time",
    }
    value.update(changes)
    return value


def fetch(payloads, monkeypatch, *, params=None, statuses=None, credentials=True):
    from jobhunt_core.harvest.providers import native_adzuna as module

    monkeypatch.setattr(module, "PAGE_DELAY_SECONDS", 0)
    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        module.settings, "CORE_ADZUNA_APP_ID", "client-id" if credentials else ""
    )
    monkeypatch.setattr(
        module.settings, "CORE_ADZUNA_APP_KEY", "client-secret" if credentials else ""
    )
    requests = []
    statuses = statuses or [200] * len(payloads)

    async def run():
        def transport(request):
            index = len(requests)
            requests.append(request)
            return httpx.Response(
                statuses[min(index, len(statuses) - 1)],
                json=payloads[min(index, len(payloads) - 1)],
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            return await module.AdzunaProvider().fetch_new(
                params if params is not None else {"country": "us"}, None, client
            )

    return asyncio.run(run()), requests


def test_two_pages_request_and_public_content_are_exact(monkeypatch):
    from jobhunt_core.harvest.admission import publication_date
    from jobhunt_core.harvest.normalize import normalize_offer
    from jobhunt_core.harvest.providers.native_adzuna import ENDPOINT, QUERY, QUERY_OR

    original = row()
    result, requests = fetch([{"results": [original]}, {"results": []}], monkeypatch)

    assert result.complete and result.error is None and result.pages_fetched == 2
    assert result.next_cursor == {"items_seen": 1, "invalid": 0}
    assert len(requests) == 2
    assert [str(request.url.copy_with(query=None)) for request in requests] == [
        f"{ENDPOINT}/us/search/1",
        f"{ENDPOINT}/us/search/2",
    ]
    assert all(
        dict(request.url.params)
        == {
            "app_id": "client-id",
            "app_key": "client-secret",
            "results_per_page": "50",
            "what": QUERY,
            "what_or": QUERY_OR,
        }
        for request in requests
    )

    listing = result.listings[0]
    assert listing.external_id == "ad-1"
    assert listing.url == original["redirect_url"]
    assert listing.payload == {
        **original,
        "_country": "us",
        "_core_category": "IT Jobs",
        "_core_order": 100,
    }
    assert normalize_offer("adzuna", listing.payload) == {
        "title": "Python Developer",
        "company": "Acme",
        "description": "Build React APIs with Docker.",
        "tags": ["developer", "python", "r", "react", "docker"],
        "salary": None,
        "location": "United States",
        "remote": False,
        "employment_type": "permanent full_time",
        "salary_min": 80000.5,
        "salary_max": 120000,
    }
    assert publication_date("adzuna", listing.payload).isoformat() == (
        "2026-10-12T10:00:00+00:00"
    )


def test_empty_pages_are_success_and_both_are_requested(monkeypatch):
    result, requests = fetch([{"results": []}, {"results": []}], monkeypatch)

    assert result.complete and result.listings == () and result.pages_fetched == 2
    assert len(requests) == 2


@pytest.mark.parametrize(
    "params",
    [{}, {"country": "at"}, {"country": "us", "query": "python"}, []],
)
def test_unverified_scope_fails_before_network(monkeypatch, params):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch([], monkeypatch, params=params)


def test_missing_credentials_fail_before_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError, match="credentials"):
        fetch([], monkeypatch, credentials=False)


@pytest.mark.parametrize("body", [None, [], {}, {"results": None}, {"jobs": []}])
def test_invalid_envelope_never_becomes_empty_success(monkeypatch, body):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch([body], monkeypatch)


def test_second_page_failure_is_partial_and_first_page_survives(monkeypatch):
    result, requests = fetch(
        [{"results": [row()]}, {"error": "busy"}],
        monkeypatch,
        statuses=[200, 503, 503, 503, 503],
    )

    assert len(requests) == 5 and len(result.listings) == 1
    assert result.pages_fetched == 1
    assert not result.complete and result.error == "page_fetch_failed"


def test_terminal_error_is_sanitized_and_does_not_expose_credentials(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError) as caught:
        fetch(
            [{"error": "busy"}] * 4,
            monkeypatch,
            statuses=[503] * 4,
        )
    message = str(caught.value)
    assert "client-id" not in message and "client-secret" not in message
    assert "http_503" in message


def test_redirect_never_forwards_credentials_to_another_host(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError
    from jobhunt_core.harvest.providers import native_adzuna as module

    monkeypatch.setattr(module.settings, "CORE_ADZUNA_APP_ID", "client-id")
    monkeypatch.setattr(module.settings, "CORE_ADZUNA_APP_KEY", "client-secret")
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.test/jobs"})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            await module.AdzunaProvider().fetch_new({"country": "us"}, None, client)

    with pytest.raises(ProviderResponseError, match="HTTP 302"):
        asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].url.host == "api.adzuna.com"


def test_invalid_neighbor_is_counted_but_valid_row_survives(monkeypatch):
    result, _requests = fetch(
        [{"results": [row(), None]}, {"results": []}], monkeypatch
    )

    assert len(result.listings) == 1
    # DT-148 E1: el ilegible se cuenta en el cursor, el barrido sigue completo.
    assert result.complete and result.error is None
    assert result.next_cursor["invalid"] == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_salary_is_removed_without_losing_the_listing(value):
    from jobhunt_core.harvest.normalize import normalize_offer
    from jobhunt_core.harvest.providers.native_adzuna import _listing
    from jobhunt_core.harvest.sink import canonical_payload

    listing = _listing(row(salary_min=value, salary_max=value), "us", 0)

    assert listing is not None
    assert listing.payload["salary_min"] is None
    assert listing.payload["salary_max"] is None
    assert "salary_min" not in normalize_offer("adzuna", listing.payload)
    assert "salary_max" not in normalize_offer("adzuna", listing.payload)
    canonical_payload(listing.payload)


def test_provider_is_registered(monkeypatch):
    from jobhunt_core.harvest.providers import get_provider

    assert get_provider("adzuna").name == "adzuna"
