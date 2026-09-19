"""Native producers must preserve raw data and distinguish failure from empty.

No source is enabled by importing a provider. Cutover still requires a scoped
canary and parity before disabling its previous writer.
"""

import asyncio
import copy

import httpx
import pytest

from jobhunt_core.harvest.normalize import normalize_offer
from jobhunt_core.harvest.provider import ProviderConfigError, ProviderResponseError


def provider(name):
    from jobhunt_core.harvest.providers.native_json import NativeJSONProvider

    return NativeJSONProvider(name)


def fetch(name, payload, params=None, status=200):
    requests = []

    async def run():
        def transport(request):
            requests.append(request)
            return httpx.Response(status, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await provider(name).fetch_new(params or {}, {}, client)
            return result

    return asyncio.run(run()), requests


@pytest.mark.parametrize("name,key,title,company", [
    ("remotive", "jobs", "title", "company_name"),
    ("workingnomads", None, "title", "company_name"),
    ("jobicy", "jobs", "jobTitle", "companyName"),
])
def test_raw_preserved_identity_does_not_depend_on_title(name, key, title, company):
    raw = {"id": 123, title: "Editor", company: "Acme", "url": "https://jobs.example/123",
           "description": "<p>First &amp; second</p>", "jobDescription": "<p>First &amp; second</p>",
           "unknown_upstream_field": {"retained": True}}
    original = copy.deepcopy(raw)
    result, _ = fetch(name, {key: [raw]} if key else [raw])
    listing = result.listings[0]
    assert listing.payload == original
    assert raw == original
    assert result.complete
    assert normalize_offer(name, listing.payload)["description"] == "First & second"
    changed = {**raw, title: "Senior Editor", "url": "https://jobs.example/renamed-123"}
    second, _ = fetch(name, {key: [changed]} if key else [changed])
    assert second.listings[0].external_id == listing.external_id


@pytest.mark.parametrize("name,payload", [
    ("remotive", {}), ("remotive", {"jobs": None}),
    ("remotive", {"jobs": {}}), ("workingnomads", {}),
    ("jobicy", []), ("jobicy", {"jobs": "changed"}),
])
def test_invalid_envelope_is_error_not_empty(name, payload):
    with pytest.raises(ProviderResponseError):
        fetch(name, payload)


@pytest.mark.parametrize("name,payload", [
    ("remotive", {"jobs": []}), ("workingnomads", []), ("jobicy", {"jobs": []}),
])
def test_explicit_empty_is_success(name, payload):
    result, _ = fetch(name, payload)
    assert result.complete and result.listings == ()


def test_unrecognized_items_cannot_claim_empty_success():
    with pytest.raises(ProviderResponseError):
        fetch("remotive", {"jobs": [None, {"id": 1, "renamed_url": "https://example.org/a"}]})


def test_bad_item_does_not_drop_valid_neighbor():
    result, _ = fetch("remotive", {"jobs": [None, {"id": 2, "url": "https://example.org/a", "title": "Editor"}]})
    assert len(result.listings) == 1


def test_http_failure_propagates():
    with pytest.raises(httpx.HTTPStatusError):
        fetch("remotive", {"jobs": []}, status=429)


def test_missing_id_uses_url_not_mutable_title():
    one, _ = fetch("workingnomads", [{"url": "https://example.org/a", "title": "A"}])
    two, _ = fetch("workingnomads", [{"url": "https://example.org/a", "title": "B"}])
    assert one.listings[0].external_id == two.listings[0].external_id


def test_jobicy_tags_are_separate_scopes_not_silent_deduped_fetches():
    _, requests = fetch("jobicy", {"jobs": []}, {"tag": "content", "geo": "europe"})
    assert len(requests) == 1
    assert requests[0].url.params["tag"] == "content"
    assert requests[0].url.params["geo"] == "europe"


def test_scope_rejects_unknown_params_before_network():
    with pytest.raises(ProviderConfigError):
        fetch("remotive", {"jobs": []}, {"endpoint": "http://localhost/private"})


def test_keyword_is_part_of_scope_identity():
    p = provider("remotive")
    assert p.params_fingerprint({"query": "editor"}) != p.params_fingerprint({"query": "teacher"})


def test_html_and_tags_are_defensive():
    result, _ = fetch("workingnomads", [{"id": 1, "url": "https://example.org/a", "title": "Editor",
                                        "description": "<script>secret()</script><p>Work</p><p>here</p>",
                                        "tags": "writing, editing"}])
    content = normalize_offer("workingnomads", result.listings[0].payload)
    assert content["description"] == "Work here"
    assert content["tags"] == ["writing", "editing"]


def test_native_registry_does_not_require_legacy_modules():
    from jobhunt_core.harvest.providers import get_provider
    from jobhunt_core.harvest.registry import ensure_handler

    for name in ("remotive", "workingnomads", "jobicy"):
        assert get_provider(name).name == name
        assert ensure_handler(name)
