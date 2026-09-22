"""Native Jobgether preserves the former identity and does not hide HTTP blocks."""
import asyncio
import hashlib

import httpx
import pytest


def raw(slug="6a2ba788135f3346537f4f73-english-teacher", **changes):
    return {"slug": slug, "title": "English teacher", "companyData": {"name": "School"},
            "requiredLocations": "Norway", "remoteOfferType": "Full Remote",
            "createdAt": "2026-09-20T00:00:00Z", "skills": [{"name": "TEFL"}],
            "salary": {"average": 100, "min": 80, "max": 120, "currency": "EUR"}, **changes}


def fetch(responses, monkeypatch):
    from jobhunt_core.harvest.providers import native_jobgether as module
    monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    calls = []
    async def run():
        def handler(request):
            calls.append(request)
            value = responses[len(calls) - 1]
            return httpx.Response(value) if isinstance(value, int) else httpx.Response(200, json=value)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await module.JobgetherProvider().fetch_new({}, None, http)
    return asyncio.run(run()), calls


def test_identity_content_and_raw(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer
    original = raw()
    result, calls = fetch([{"data": [original], "maxPages": "1"}], monkeypatch)
    assert result.complete and len(calls) == 1
    row = result.listings[0]
    expected = hashlib.md5(b"english teacher|school|https://jobgether.com/offer/english-teacher").hexdigest()
    assert row.external_id == expected and row.payload == original
    assert row.url.endswith(original["slug"])
    content = normalize_offer("jobgether", row.payload)
    assert content["company"] == "School" and content["remote"] is True
    assert content["location"] == "Norway" and content["salary"] == "80-120 EUR"
    assert content["tags"] == ["TEFL", "english"]


def test_relisting_keeps_identity_but_real_url_changes(monkeypatch):
    first, _ = fetch([{"data": [raw()], "maxPages": 1}], monkeypatch)
    second, _ = fetch([{"data": [raw("7a2ba788135f3346537f4f73-english-teacher")], "maxPages": 1}], monkeypatch)
    assert first.listings[0].external_id == second.listings[0].external_id
    assert first.listings[0].url != second.listings[0].url


@pytest.mark.parametrize("status", [403, 429, 503])
def test_block_is_not_successful_empty_feed(monkeypatch, status):
    with pytest.raises(httpx.HTTPStatusError):
        fetch([status], monkeypatch)


def test_late_failure_preserves_prefix(monkeypatch):
    result, calls = fetch([{"data": [raw()], "maxPages": 2}, 403], monkeypatch)
    assert len(calls) == 2 and len(result.listings) == 1
    assert not result.complete and result.error == "http_403"


def test_page_cap_is_explicitly_partial(monkeypatch):
    result, calls = fetch([{"data": [raw()], "maxPages": 4}] * 3, monkeypatch)
    assert len(calls) == 3 and not result.complete
    assert result.error == "page_budget"


@pytest.mark.parametrize("body", [None, [], {}, {"data": {}}, {"data": [], "maxPages": True}])
def test_invalid_envelope_not_a_green_empty(monkeypatch, body):
    from jobhunt_core.harvest.provider import ProviderResponseError
    with pytest.raises(ProviderResponseError):
        fetch([body], monkeypatch)


def test_invalid_neighbor_and_missing_date_preserve_raw(monkeypatch):
    original = raw(createdAt=None)
    result, _ = fetch([{"data": [original, raw(slug="../../other")], "maxPages": 1}], monkeypatch)
    assert len(result.listings) == 1 and not result.complete
    assert result.listings[0].payload["createdAt"] is None


def test_non_remote_and_strange_optional_data(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer
    result, _ = fetch([{"data": [raw(remoteOfferType="On-site", skills=None, salary=[])], "maxPages": 1}], monkeypatch)
    content = normalize_offer("jobgether", result.listings[0].payload)
    assert content["remote"] is False and content["salary"] is None


def test_ambiguous_relisting_group_is_not_arbitrated_by_order(monkeypatch):
    rows = [raw(), raw("7a2ba788135f3346537f4f73-english-teacher"), raw("other-teacher")]
    result, _ = fetch([{"data": rows, "maxPages": 1}], monkeypatch)
    assert [r.url for r in result.listings] == ["https://jobgether.com/offer/other-teacher"]
    # Dropping the group is still the contract; what changed at handover is its
    # CLASSIFICATION. The portal republishes one opening as several postings on
    # every sweep, so reporting a failed harvest would keep `last_complete_at`
    # NULL forever. The count moved to the cursor — see
    # test_native_page_budget.py::test_portal_duplicate_postings_are_dropped_*
    assert result.next_cursor["ambiguous"] == 1


def test_oversized_page_number_preserves_valid_prefix(monkeypatch):
    result, _ = fetch([{"data": [raw()], "maxPages": 2},
                       {"data": [], "maxPages": "9" * 5000}], monkeypatch)
    assert len(result.listings) == 1 and not result.complete
    assert result.error == "ProviderResponseError"


def test_unicode_slug_is_not_lost(monkeypatch):
    result, _ = fetch([{"data": [raw("enseignant-éducateur")], "maxPages": 1}], monkeypatch)
    assert result.complete and len(result.listings) == 1


def test_large_json_integer_does_not_crash_normalization(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer
    result, _ = fetch([{"data": [raw(salary={"average": 10**400, "min": 80})], "maxPages": 1}], monkeypatch)
    assert normalize_offer("jobgether", result.listings[0].payload)["salary"] == "80"


def test_wired_date_and_provider():
    from jobhunt_core.harvest.admission import publication_date
    from jobhunt_core.harvest.providers import get_provider
    assert get_provider("jobgether").name == "jobgether"
    assert publication_date("jobgether", raw()).isoformat() == "2026-09-20T00:00:00+00:00"


@pytest.mark.parametrize("slug,kept", [
    ("6ab14dd3865119c687d6c9df-frontend-engineer-react-next.js", True),
    ("6ab14dd3865119c687d6c9d3-senior-full-stack-developer-.net", True),
    ("6ab14dcf865119c687d6c62e-phd-or-psy.d-school-psychologist", True),
    ("../../etc/passwd", False),          # traversal, never interpolated
    ("job/../admin", False),
    ("job?utm=x", False),                 # would fabricate a second URL
    ("job#frag", False),
    ("job%2F..", False),
])
def test_a_dot_in_the_slug_is_legitimate_but_traversal_is_not(slug, kept, monkeypatch):
    """Live probe 2026-09-22: 6 of 150 offers were dropped for having a dot.

    Technology names carry dots (next.js, .NET, Psy.D) and the retiring
    producer keeps those slugs untouched, so rejecting them lost real coverage.
    What must stay rejected is anything that could change the resolved URL.
    """
    result, _ = fetch([{"data": [raw(slug), raw("plain-other")], "maxPages": 1}], monkeypatch)
    urls = [listing.url for listing in result.listings]
    assert (f"https://jobgether.com/offer/{slug}" in urls) is kept
    assert "https://jobgether.com/offer/plain-other" in urls, "the valid one always survives"
