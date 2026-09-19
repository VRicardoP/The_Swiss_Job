"""Native CH Media boundary, pagination and parity contracts."""
import asyncio
import copy
import json

import httpx
import pytest

from jobhunt_core.harvest.normalize import normalize_offer
from jobhunt_core.harvest.provider import ProviderConfigError, ProviderResponseError


def fetch(name="ostjob", pages=None, params=None, cursor=None, monkeypatch=None):
    from jobhunt_core.harvest.providers import native_chmedia as module
    if monkeypatch:
        monkeypatch.setattr(module, "PAGE_PAUSE_S", 0)
    seen = []
    async def run():
        def transport(request):
            seen.append(request)
            page = int(request.url.params["page"])
            value = pages[page - 1]
            if isinstance(value, int):
                return httpx.Response(value)
            if isinstance(value, dict) and isinstance(value.get("items"), list):
                # Model the real envelope. Explicit malformed metadata in
                # boundary tests is never replaced by fixture defaults.
                value = {"pages": len(pages), **value}
            # JSON escapes can contain a lone surrogate on the real wire.
            return httpx.Response(200, content=json.dumps(value).encode())
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            return await module.CHMediaProvider(name).fetch_new(params or {}, cursor, http)
    return asyncio.run(run()), seen


def raw(i=1, **values):
    return {"id": i, "externalId": str(i), "title": "English teacher", "company": {"name": "Academy"},
            "workplaceCity": "Zurich", "cantons": ["ZH"], "activity": "<p>TEFL &amp; English</p>",
            "keywords": "language, language", "homeOffice": True,
            "dateFirstPublished": "2026-09-19T10:00:00Z", "unknown": {"kept": True}, **values}


@pytest.mark.parametrize("name", ["ostjob", "zentraljob"])
def test_raw_and_identity_preserved_with_legacy_content(name):
    row = raw()
    original = copy.deepcopy(row)
    result, seen = fetch(name, [{"items": [row]}])
    listing, = result.listings
    assert row == original == listing.payload
    assert listing.url == f"https://{name}.ch/stelle/1"
    assert result.complete and result.pages_fetched == 1
    assert seen[0].url.host == f"api.{name}.ch"
    content = normalize_offer(name, row)
    assert content["description"] == "TEFL &amp; English"
    assert content["location"] == "Zurich, ZH"
    assert content["company"] == "Academy" and content["remote"] is True
    assert content["tags"] == ["language", "english", "tefl"]
    changed, _ = fetch(name, [{"items": [raw(title="New title")]}])
    assert changed.listings[0].external_id == listing.external_id


@pytest.mark.parametrize("values,url", [
    ({"urlApplication": "https://company.example/apply/1"}, "https://company.example/apply/1"),
    ({"urlApplication": "mailto:hr@example.org", "urlDescription": "https://company.example/1"}, "https://company.example/1"),
    ({"externalId": "", "urlDescription": "https://company.example/1"}, "https://company.example/1"),
])
def test_url_fallback(values, url):
    result, _ = fetch(pages=[{"items": [raw(**values)]}])
    assert result.listings[0].url == "https://ostjob.ch/stelle/1"
    assert result.listings[0].apply_url == url


@pytest.mark.parametrize("body", [{}, [], {"items": None}, {"items": {}}, {"items": "bad"}])
def test_invalid_first_envelope_raises(body):
    with pytest.raises(ProviderResponseError):
        fetch(pages=[body])


def test_empty_feed_is_complete():
    result, _ = fetch(pages=[{"items": []}])
    assert result.complete and result.listings == ()


def test_invalid_neighbor_is_visible_partial():
    result, _ = fetch(pages=[{"items": [raw(), None, {"title": "No identity"}]}])
    assert len(result.listings) == 1 and not result.complete
    assert result.error == "invalid_chmedia_items"
    with pytest.raises(ProviderResponseError):
        fetch(pages=[{"items": [None, {}]}])


@pytest.mark.parametrize("last", [{}, {"items": None}, 429, 500])
def test_late_failure_preserves_previous_page(monkeypatch, last):
    result, seen = fetch(pages=[{"items": [raw(i) for i in range(1, 21)]}, last], monkeypatch=monkeypatch)
    assert len(result.listings) == 20 and result.pages_fetched == 1
    assert not result.complete and result.error and len(seen) == 2


def test_full_page_budget_is_partial_and_next_sweep_restarts(monkeypatch):
    from jobhunt_core.harvest.providers import native_chmedia as module
    monkeypatch.setattr(module, "MAX_PAGES", 2)
    pages = [{"items": [raw(page*20+i) for i in range(1, 21)], "pages": 3} for page in range(2)]
    result, seen = fetch(pages=pages, monkeypatch=monkeypatch)
    assert len(result.listings) == 40 and not result.complete and result.error is None
    repeated, requests = fetch(pages=pages, cursor=result.next_cursor, monkeypatch=monkeypatch)
    assert requests[0].url.params["page"] == "1"
    assert repeated.listings == result.listings


def test_short_final_page_and_invalid_params(monkeypatch):
    result, _ = fetch(pages=[{"items": [raw(i) for i in range(1, 21)]}, {"items": [raw(21)]}], monkeypatch=monkeypatch)
    assert result.complete and len(result.listings) == 21
    with pytest.raises(ProviderConfigError):
        fetch(pages=[], params={"endpoint": "http://localhost/"})


def test_unknown_source_rejected():
    with pytest.raises(ProviderConfigError):
        fetch("unknown", [])
