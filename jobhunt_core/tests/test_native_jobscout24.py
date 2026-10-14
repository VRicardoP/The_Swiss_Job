"""JobScout24 keeps its keyword sweep, its selectors and its bounded details."""

import asyncio
import json

import httpx
import pytest

BADGES = ("Top Listing", "100%", "Large companies")


def card(
    i=1, *, badges=BADGES, date="2 d", detail=True, title=None, location="Z&#252;rich"
):
    path = f'data-job-detail-url="/en/job/uuid-{i}/"' if detail else ""
    tags = "".join(
        f'<li><span class="tag tag-readonly">\n\t{badge}\n</span></li>'
        for badge in badges
    )
    return f"""
    <li class="job-list-item " data-job-id="job-{i}" {path}>
      <div class="upper-line">
        <a href="/en/job/uuid-{i}/" class="job-link-detail job-title"
           title="x">{"Python Developer" if title is None else title}</a>
      </div>
      <p class="job-attributes"><span>ACME</span>, <span>{location}</span></p>
      <div class="lower-line">
        <div class="job-tags"><ul>{tags}</ul></div>
        <p class="job-date">\n\t{date}\n</p>
      </div>
    </li>
    """


def page(cards_html=""):
    return f'<html><body><ul class="job-list">{cards_html}</ul></body></html>'


def detail(description="<p>Build <strong>APIs</strong> and ship.</p>"):
    payload = json.dumps({"@type": "JobPosting", "description": description})
    return f'<script type="application/ld+json">{payload}</script>'


def fetch(pages, *, cursor=None, params=None, monkeypatch=None, details=detail()):
    """Run one sweep. `pages` maps (keyword, page) -> html, with a default."""
    from jobhunt_core.harvest.providers import native_jobscout24 as module

    if monkeypatch:
        monkeypatch.setattr(module, "REQUEST_PAUSE_S", 0)
    requests = []

    async def run():
        def transport(request):
            requests.append(str(request.url))
            if request.url.path.startswith("/en/jobs/"):
                keyword = request.url.path.split("/")[3]
                number = int(request.url.params.get("p", 1))
                body = pages.get((keyword, number), pages.get("default", page()))
                return httpx.Response(200, text=body)
            return httpx.Response(200, text=details)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await module.JobScout24Provider().fetch_new(
                {} if params is None else params, cursor, client
            )
        return result, requests

    return asyncio.run(run())


def test_public_fields_selectors_and_detail_are_preserved(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, requests = fetch({"default": page(card())}, monkeypatch=monkeypatch)

    assert result.complete and result.error is None and len(result.listings) == 1
    # Five keywords x two pages, then one bounded detail page.
    assert len(requests) == 11
    assert requests[0] == (
        "https://www.jobscout24.ch/en/jobs/informatik/?p=1&SortIndex=1"
    )
    listing = result.listings[0]
    assert listing.external_id == "job-1"
    assert listing.url == "https://www.jobscout24.ch/en/job/uuid-1/"
    assert listing.payload["date"] == result.listings[0].payload["date"]
    assert listing.payload["_core_order"] == 0
    # Sorted on the original case (as the producer sorts), then folded for
    # statistics; the technologies come from title + description.
    assert listing.payload["_core_statistics_tags"] == [
        "large companies",
        "developer",
        "python",
    ]
    assert result.next_cursor["descriptions"] == {"job-1": "Build APIs and ship."}
    assert normalize_offer("jobscout24", listing.payload) == {
        "title": "Python Developer",
        "company": "ACME",
        "description": "Build APIs and ship.",
        "tags": ["Large companies", "developer", "python"],
        "salary": None,
        "location": "Zürich",
        "remote": False,
        "employment_type": "100%",
    }


def test_sponsored_badge_and_workload_never_become_tags(monkeypatch):
    result, _requests = fetch(
        {"default": page(card(badges=("Top Listing", "80% - 100%", "Home office")))},
        monkeypatch=monkeypatch,
        details=detail("plain text"),
    )
    payload = result.listings[0].payload

    assert payload["badges"] == ["80% - 100%", "Home office"]
    assert payload["_core_statistics_tags"] == [
        "home office",
        "developer",
        "python",
    ]
    from jobhunt_core.harvest.normalize import normalize_offer

    content = normalize_offer("jobscout24", payload)
    assert content["employment_type"] == "80% - 100%"
    assert content["tags"] == ["Home office", "developer", "python"]
    assert content["remote"] is True


def test_keywords_are_globally_deduplicated_and_reuse_the_cursor(monkeypatch):
    result, requests = fetch(
        {"default": page(card())},
        cursor={"descriptions": {"job-1": "Cached"}},
        monkeypatch=monkeypatch,
    )

    assert len(result.listings) == 1
    # No detail request: the cursor already had that description.
    assert len(requests) == 10
    assert result.next_cursor["items_seen"] == 1
    assert result.listings[0].payload["_core_description"] == "Cached"


def test_an_empty_page_stops_that_keyword_only(monkeypatch):
    pages = {"default": page()}
    pages[("informatik", 1)] = page(card(1))
    pages[("informatik", 2)] = page(card(2))
    result, requests = fetch(pages, monkeypatch=monkeypatch)

    listing_requests = [url for url in requests if "/en/jobs/" in url]
    # informatik exhausts both pages; the other four stop after their page 1.
    assert len(listing_requests) == 6
    assert {listing.external_id for listing in result.listings} == {"job-1", "job-2"}


def test_recent_order_is_date_descending_then_sweep_order(monkeypatch):
    pages = {"default": page()}
    pages[("informatik", 1)] = page(
        card(1, date="5 d") + card(2, date="1 d") + card(3, date="5 d")
    )
    result, _requests = fetch(pages, monkeypatch=monkeypatch)
    order = {
        listing.external_id: listing.payload["_core_order"]
        for listing in result.listings
    }

    assert order == {"job-2": 0, "job-1": 1, "job-3": 2}
    assert result.next_cursor["recent_items"] == 3


def test_card_without_detail_url_is_dropped_and_reported(monkeypatch):
    pages = {"default": page()}
    pages[("informatik", 1)] = page(card(1) + card(2, detail=False))
    result, _requests = fetch(pages, monkeypatch=monkeypatch)

    assert [listing.external_id for listing in result.listings] == ["job-1"]
    assert not result.complete and result.error == "invalid_jobscout24_cards"


def test_an_empty_sweep_fails_instead_of_emptying_the_corpus(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError, match="no cards"):
        fetch({"default": page()}, monkeypatch=monkeypatch)


def test_a_blocked_listing_is_a_failure_not_an_empty_page(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError
    from jobhunt_core.harvest.providers import native_jobscout24 as module

    monkeypatch.setattr(module, "MAX_RETRIES", 0)
    with pytest.raises(ProviderResponseError, match="request failed"):
        fetch(
            {"default": "<html>Attention Required! | Cloudflare</html>"},
            monkeypatch=monkeypatch,
        )


def test_a_page_with_jobs_may_mention_captcha(monkeypatch):
    pages = {"default": page()}
    pages[("informatik", 1)] = "<p>Solve the captcha on our form</p>" + page(card(1))
    result, _requests = fetch(pages, monkeypatch=monkeypatch)

    assert [listing.external_id for listing in result.listings] == ["job-1"]


@pytest.mark.parametrize(
    "cursor",
    [
        [],
        {"descriptions": []},
        {"descriptions": {"job-1": ""}},
        {"descriptions": {1: "x"}},
    ],
)
def test_corrupt_description_cursor_fails_closed(cursor, monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch({"default": page(card())}, cursor=cursor, monkeypatch=monkeypatch)


def test_invalid_scope_fails_before_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch({}, params={"keyword": "python"}, monkeypatch=monkeypatch)


def test_cold_cursor_fetches_at_most_twenty_details(monkeypatch):
    pages = {"default": page()}
    pages[("informatik", 1)] = page("".join(card(i) for i in range(1, 23)))
    result, requests = fetch(pages, monkeypatch=monkeypatch)

    assert len(result.listings) == 22
    assert len([url for url in requests if "/en/job/" in url]) == 20
    assert result.next_cursor["detail_attempts"] == 20
    assert result.next_cursor["details_fetched"] == 20
    assert result.listings[-1].payload["_core_description"] == ""


def test_relative_dates_cover_every_unit_and_unparseable_markers():
    from datetime import datetime, timezone

    from jobhunt_core.harvest.providers.native_jobscout24 import relative_date_to_iso

    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    assert relative_date_to_iso("30+ d", now) == "2026-09-14"
    assert relative_date_to_iso("2 w", now) == "2026-09-30"
    assert relative_date_to_iso("5 h", now) == "2026-10-14"
    assert relative_date_to_iso("10 min", now) == "2026-10-14"
    # Non-empty but unparseable must not sort to the end of the board.
    assert relative_date_to_iso("just now", now) == "2026-10-14"
    assert relative_date_to_iso("", now) == ""


def test_location_defaults_to_switzerland_and_blank_folds_to_worldwide():
    from jobhunt_core.harvest.providers.native_jobscout24 import _location

    assert _location("") == "Switzerland"
    assert _location(None) == "Switzerland"
    assert _location("   ") == "Worldwide"
    assert _location("remote") == "Worldwide"
    assert _location("Vattiz") == "Vattiz"


def test_cards_parser_reproduces_the_producer_selectors():
    from jobhunt_core.harvest.providers.native_jobscout24 import _cards

    html = page(
        card(1, location="Bern / Z&#252;rich")
        + '<li class="job-list-item " data-job-id="" data-job-detail-url="/x/">'
        '<a class="job-link-detail">No id</a></li>'
        + '<li class="job-list-item " data-job-id="job-9" '
        'data-job-detail-url="/en/job/uuid-9/"><a class="job-link-detail">'
        "</a></li>"
    )
    cards = _cards(html)

    # A card is defined by its id AND its title: the others are not cards.
    assert [row["id"] for row in cards] == ["job-1"]
    assert cards[0]["company"] == "ACME"
    assert cards[0]["location"] == "Bern / Zürich"
    assert cards[0]["badges"] == ["100%", "Large companies"]
    assert cards[0]["detail_path"] == "/en/job/uuid-1/"


def test_card_text_is_joined_like_the_producer():
    from jobhunt_core.harvest.providers.native_jobscout24 import _cards

    html = (
        '<li class="job-list-item " data-job-id="job-1" data-job-detail-url="/d/">'
        '<a class="job-link-detail">Senior <em>Python</em><br> Dev</a>'
        '<p class="job-attributes"><span>A<b>CME</b></span><span>Zug</span></p>'
        "</li>"
    )
    cards = _cards(html)

    assert cards[0]["title"] == "SeniorPythonDev"
    assert cards[0]["company"] == "ACME"
    assert cards[0]["badges"] == [] and cards[0]["date"] == ""


def test_published_at_uses_the_card_date():
    from datetime import datetime, timezone

    from jobhunt_core.harvest.admission import publication_date

    assert publication_date("jobscout24", {"date": "2026-10-08"}) == datetime(
        2026, 10, 8, tzinfo=timezone.utc
    )
    assert publication_date("jobscout24", {"date": ""}) is None


def test_response_byte_budget_fails_closed(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError
    from jobhunt_core.harvest.providers import native_jobscout24 as module

    monkeypatch.setattr(module, "MAX_RETRIES", 0)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"1234")
            )
        ) as client:
            return await module._read(client, "https://www.jobscout24.ch/x", 3)

    with pytest.raises(ProviderResponseError, match="byte budget"):
        asyncio.run(run())


def test_soft_block_detection_keeps_the_producers_whole_list():
    from jobhunt_core.harvest.providers.html_response import looks_like_soft_block

    assert looks_like_soft_block("page with cf-chl-bypass token")
    assert looks_like_soft_block("Please complete the captcha")
    assert looks_like_soft_block("ACCESS DENIED")
    # Widgets on legitimate application forms are not challenges.
    assert not looks_like_soft_block('<script src="/recaptcha/api.js">')
    assert not looks_like_soft_block("h-captcha-response")
    assert not looks_like_soft_block(None) and not looks_like_soft_block("")
