"""SwissDevJobs keeps one light sweep and enriches at most 20 rows per run."""

import asyncio
import json

import httpx
import pytest

_DEFAULT = object()


def row(i=1, **changes):
    value = {
        "_id": f"mongo-{i}",
        "jobUrl": f"job-{i}",
        "isPaused": False,
        "name": "Senior Python Engineer",
        "company": "ACME",
        "cityCategory": "zurich",
        "actualCity": "Zürich",
        "technologies": [f"Tech {n}" for n in range(12)],
        "annualSalaryFrom": 100000,
        "annualSalaryTo": 130000,
        "logoImg": "acme.png",
        "workplace": "hybrid",
        "remoteType": "anywhere",
        "jobType": "Full-Time",
        "activeFrom": "2026-10-13T00:00:00.000+02:00",
    }
    value.update(changes)
    return value


def detail(description="<p>Build <strong>APIs</strong> &amp; ship.</p>"):
    return (
        "<html><script>window.__detailedJob="
        + json.dumps({"description": description})
        + ";</script></html>"
    )


def fetch(
    rows,
    *,
    cursor=None,
    details=None,
    feed_body=_DEFAULT,
    params=None,
    monkeypatch=None,
):
    from jobhunt_core.harvest.providers import native_swissdevjobs as module

    if monkeypatch:
        monkeypatch.setattr(module, "DETAIL_PAUSE_SECONDS", 0)
    requests = []

    async def run():
        def transport(request):
            requests.append(request)
            if request.url.path == "/api/jobsLight":
                body = rows if feed_body is _DEFAULT else feed_body
                return httpx.Response(200, content=json.dumps(body).encode())
            slug = request.url.path.rsplit("/", 1)[1]
            value = (details or {}).get(slug, detail())
            return (
                httpx.Response(value)
                if type(value) is int
                else httpx.Response(200, text=value)
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            result = await module.SwissDevJobsProvider().fetch_new(
                {} if params is None else params, cursor, client
            )
            return result, requests

    return asyncio.run(run())


def test_light_feed_cursor_and_public_fields_are_preserved(monkeypatch):
    from jobhunt_core.harvest.normalize import normalize_offer

    result, requests = fetch(
        [row(), row(2, cityCategory="Remote", workplace="remote", remoteType=None)],
        cursor={"descriptions": {"job-1": "Cached text", "stale": "drop me"}},
        monkeypatch=monkeypatch,
    )

    assert result.complete and result.error is None and len(result.listings) == 2
    assert [request.url.path for request in requests] == [
        "/api/jobsLight",
        "/jobs/job-2",
    ]
    assert result.next_cursor["descriptions"] == {
        "job-1": "Cached text",
        "job-2": "Build APIs &amp; ship.",
    }
    listing = result.listings[1]
    assert listing.external_id == "job-2"
    assert listing.url == "https://swissdevjobs.ch/jobs/job-2"
    assert listing.payload["_id"] == "mongo-2"

    content = normalize_offer("swissdevjobs", listing.payload)
    assert content == {
        "title": "Senior Python Engineer",
        "company": "ACME",
        "description": "Build APIs &amp; ship.",
        "tags": [f"Tech {n}" for n in range(10)],
        "salary": None,
        "location": "Switzerland",
        "remote": True,
        "salary_currency": "CHF",
        "employment_type": "Full-Time",
        "logo": "https://static.swissdevjobs.ch/logo-images/acme.png",
        "salary_min": 100000,
        "salary_max": 130000,
    }


def test_cold_cursor_fetches_at_most_twenty_details(monkeypatch):
    result, requests = fetch([row(i) for i in range(1, 22)], monkeypatch=monkeypatch)

    assert result.complete and len(result.listings) == 21
    assert len(requests) == 21
    assert result.next_cursor["detail_attempts"] == 20
    assert result.next_cursor["details_fetched"] == 20
    assert len(result.next_cursor["descriptions"]) == 20
    assert result.listings[-1].payload["_core_description"] == ""


def test_detail_failures_are_retriable_without_failing_the_light_sweep(monkeypatch):
    result, _requests = fetch(
        [row(), row(2)],
        details={"job-1": 503, "job-2": "<html>no job payload</html>"},
        monkeypatch=monkeypatch,
    )

    assert result.complete and result.error is None
    assert result.next_cursor["detail_attempts"] == 2
    assert result.next_cursor["details_fetched"] == 0
    assert result.next_cursor["descriptions"] == {}
    assert all(not listing.payload["_core_description"] for listing in result.listings)


def test_paused_rows_are_a_valid_empty_board(monkeypatch):
    result, requests = fetch([row(isPaused=True)], monkeypatch=monkeypatch)
    assert result.complete and result.listings == () and len(requests) == 1


def test_invalid_item_keeps_valid_rows_but_marks_the_sweep_partial(monkeypatch):
    result, _requests = fetch([row(), None], monkeypatch=monkeypatch)
    assert len(result.listings) == 1
    assert not result.complete and result.error == "invalid_swissdevjobs_items"


def test_active_from_is_the_declared_portal_date():
    from datetime import datetime

    from jobhunt_core.harvest.admission import publication_date

    assert publication_date("swissdevjobs", row()) == datetime.fromisoformat(
        "2026-10-13T00:00:00.000+02:00"
    )


@pytest.mark.parametrize("body", [None, {}, {"jobs": []}, "changed"])
def test_invalid_feed_envelope_never_becomes_empty_success(body, monkeypatch):
    from jobhunt_core.harvest.provider import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        fetch([], feed_body=body, monkeypatch=monkeypatch)


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
        fetch([row()], cursor=cursor, monkeypatch=monkeypatch)


def test_invalid_filters_fail_before_network(monkeypatch):
    from jobhunt_core.harvest.provider import ProviderConfigError

    with pytest.raises(ProviderConfigError):
        fetch([], params={"query": "python"}, monkeypatch=monkeypatch)


def test_description_parser_strips_html_and_caps_legacy_length():
    from jobhunt_core.harvest.providers.native_swissdevjobs import _description

    value = _description(detail("<p>" + "x" * 600 + "</p>"))
    assert value == "x" * 500
    assert _description("<html>no marker</html>") == ""
