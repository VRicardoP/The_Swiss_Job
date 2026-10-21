"""JSearch preserves the verified daily and live-search contracts."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest


def row(identity="js-1", **changes):
    value = {
        "job_id": identity,
        "job_title": "Senior Python Developer",
        "employer_name": "Acme Corp",
        "employer_logo": "https://cdn.example/logo.png",
        "job_city": "New York",
        "job_state": "NY",
        "job_country": "US",
        "job_is_remote": False,
        "job_employment_type": "FULLTIME",
        "job_publisher": "LinkedIn",
        "job_apply_link": f"https://jobs.example/{identity}",
        "job_posted_at_datetime_utc": "2026-10-12T10:00:00Z",
        "job_min_salary": 80000.5,
        "job_max_salary": 120000,
        "job_salary_currency": "USD",
        "job_description": "Build Python APIs.",
    }
    value.update(changes)
    return value


def fetch(
    payload,
    monkeypatch,
    *,
    params=None,
    search_params=None,
    status=200,
    credentials=True,
):
    from jobhunt_core.harvest.providers import native_jsearch as module
    from jobhunt_core.provision_jsearch import SCOPE_PARAMS

    monkeypatch.setattr(module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        module.settings,
        "CORE_JSEARCH_RAPIDAPI_KEY",
        "rapid-secret" if credentials else "",
    )
    requests = []

    async def run():
        def transport(request):
            requests.append(request)
            return httpx.Response(status, json=payload)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            return await module.JSearchProvider(search_params).fetch_new(
                params if params is not None else SCOPE_PARAMS[0], None, client
            )

    return asyncio.run(run()), requests


def test_daily_request_content_and_normalization_are_exact(monkeypatch):
    from jobhunt_core.harvest.admission import publication_date
    from jobhunt_core.harvest.normalize import normalize_offer
    from jobhunt_core.harvest.providers.native_jsearch import ENDPOINT, RAPIDAPI_HOST

    original = row()
    result, requests = fetch({"data": [original]}, monkeypatch)

    assert result.complete and result.error is None and result.pages_fetched == 10
    assert result.next_cursor == {
        "items_seen": 1,
        "invalid": 0,
        "last_query": "software developer",
    }
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url.copy_with(query=None)) == ENDPOINT
    assert dict(request.url.params) == {
        "query": "software developer",
        "page": "1",
        "num_pages": "10",
        "country": "us",
        "date_posted": "month",
    }
    assert request.headers["x-rapidapi-host"] == RAPIDAPI_HOST
    assert request.headers["x-rapidapi-key"] == "rapid-secret"

    listing = result.listings[0]
    assert listing.external_id == "js-1"
    assert listing.url.startswith("https://jsearch.p.rapidapi.com/job/")
    assert listing.apply_url == original["job_apply_link"]
    assert listing.payload == {**original, "_core_order": 0}
    assert normalize_offer("jsearch", listing.payload) == {
        "title": "Senior Python Developer",
        "company": "Acme Corp",
        "description": "Build Python APIs.",
        "location": "New York, NY, US",
        "tags": [],
        "remote": False,
        "salary": None,
        "salary_min": 80000.5,
        "salary_max": 120000,
        "salary_currency": "USD",
        "employment_type": "FULLTIME",
        "logo": "https://cdn.example/logo.png",
    }
    assert publication_date("jsearch", listing.payload).isoformat() == (
        "2026-10-12T10:00:00+00:00"
    )


def test_daily_harvest_deduplicates_job_id(monkeypatch):
    result, _requests = fetch({"data": [row(), row()]}, monkeypatch)
    assert len(result.listings) == 1
    assert result.complete


def test_manual_harvest_reuses_provider_with_custom_query(monkeypatch):
    from jobhunt_core.harvest.providers.native_jsearch import SearchParams
    from jobhunt_core.provision_jsearch import SCOPE_PARAMS

    params = SearchParams(
        query="data scientist",
        page=1,
        num_pages=10,
        country="us",
        date_posted="month",
    )
    result, requests = fetch(
        {"data": [row()]},
        monkeypatch,
        search_params=params,
        params=SCOPE_PARAMS[0],
    )

    assert dict(requests[0].url.params)["query"] == "data scientist"
    assert result.next_cursor["last_query"] == "data scientist"


def test_long_provider_id_uses_stable_internal_hash(monkeypatch):
    value = "provider-id-" + "x" * 300
    result, _requests = fetch({"data": [row(value)]}, monkeypatch)

    assert result.listings[0].external_id.startswith("sha256:")
    assert len(result.listings[0].external_id) == 71
    assert result.listings[0].payload["job_id"] == value


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"query": "python"},
        {
            "query": "software developer",
            "country": "ch",
            "date_posted": "month",
            "page": 1,
            "num_pages": 10,
        },
        [],
    ],
)
def test_unverified_scope_fails_before_network(monkeypatch, params):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch({"data": []}, monkeypatch, params=params)


def test_missing_credential_fails_before_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError, match="credential"):
        fetch({"data": []}, monkeypatch, credentials=False)


@pytest.mark.parametrize("payload", [None, [], {}, {"jobs": []}, {"data": None}])
def test_invalid_envelope_never_becomes_empty_success(monkeypatch, payload):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch(payload, monkeypatch)


def test_redirect_never_forwards_credential(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError
    from jobhunt_core.harvest.providers import native_jsearch as module
    from jobhunt_core.provision_jsearch import SCOPE_PARAMS

    monkeypatch.setattr(module.settings, "CORE_JSEARCH_RAPIDAPI_KEY", "rapid-secret")
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.test/jobs"})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            await module.JSearchProvider().fetch_new(SCOPE_PARAMS[0], None, client)

    with pytest.raises(ProviderResponseError, match="HTTP 302"):
        asyncio.run(run())
    assert len(requests) == 1 and requests[0].url.host == "jsearch.p.rapidapi.com"


def test_terminal_error_is_sanitized(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError) as caught:
        fetch({"message": "busy"}, monkeypatch, status=503)
    message = str(caught.value)
    assert "rapid-secret" not in message
    assert "http_503" in message


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_salaries_are_removed(value):
    from jobhunt_core.harvest.normalize import normalize_offer
    from jobhunt_core.harvest.providers.native_jsearch import _listing, live_job
    from jobhunt_core.harvest.sink import canonical_payload

    listing = _listing(row(job_min_salary=value, job_max_salary=value), 0)
    assert listing is not None
    assert listing.payload["job_min_salary"] is None
    assert listing.payload["job_max_salary"] is None
    content = normalize_offer("jsearch", listing.payload)
    assert "salary_min" not in content and "salary_max" not in content
    assert (
        live_job(row(job_min_salary=value, job_max_salary=value))["min_salary"] is None
    )
    canonical_payload(listing.payload)


def test_live_result_preserves_public_shape():
    from jobhunt_core.harvest.providers.native_jsearch import live_job

    raw = row(job_city="", job_state="", job_country="", job_is_remote=True)
    result = live_job(raw)
    assert result == {
        "job_id": "js-1",
        "title": "Senior Python Developer",
        "employer": "Acme Corp",
        "employer_logo": "https://cdn.example/logo.png",
        "location": "Remote",
        "is_remote": True,
        "employment_type": "FULLTIME",
        "publisher": "LinkedIn",
        "apply_link": "https://jobs.example/js-1",
        "posted_at": "2026-10-12T10:00:00Z",
        "min_salary": 80000.5,
        "max_salary": 120000,
        "salary_currency": "USD",
        "description_snippet": "Build Python APIs....",
    }


def test_provider_is_registered():
    from jobhunt_core.harvest.providers import get_provider

    assert get_provider("jsearch").name == "jsearch"
