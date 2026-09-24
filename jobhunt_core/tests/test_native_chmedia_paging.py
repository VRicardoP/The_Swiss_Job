"""Real CH Media ignores size=20, returns 10, and exposes total `pages`."""

import pytest

from jobhunt_core.tests.test_native_chmedia import fetch, raw


def test_short_page_is_not_end_when_portal_says_more(monkeypatch):
    pages = [
        {"items": [raw(page * 10 + i) for i in range(1, 11)], "pages": 2, "total": 20}
        for page in range(2)
    ]
    result, requests = fetch(pages=pages, monkeypatch=monkeypatch)
    assert len(result.listings) == 20 and len(requests) == 2
    assert result.complete


def test_full_final_page_can_be_complete_from_metadata(monkeypatch):
    from jobhunt_core.harvest.providers import native_chmedia as module

    monkeypatch.setattr(module, "MAX_PAGES", 1)
    result, _ = fetch(
        pages=[{"items": [raw(i) for i in range(1, 21)], "pages": 1, "total": 20}],
        monkeypatch=monkeypatch,
    )
    assert result.complete


@pytest.mark.parametrize("pages", [None, True, "2", -1, {}])
def test_malformed_page_metadata_is_error_not_end(pages):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch(pages=[{"items": [raw()], "pages": pages}])


def test_empty_page_contradicting_page_count_is_error():
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch(pages=[{"items": [], "pages": 4}])
