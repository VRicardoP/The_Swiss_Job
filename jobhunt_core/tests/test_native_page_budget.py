"""NAV and Jobgether must harvest exactly what the retiring producers harvest.

Both legacy providers stop at three pages (nav_arbeidsplassen.py:56,
jobgether.py:57) and both send browser headers — Jobgether's API answers 403
without them (jobgether.py:49-50). The native adapters instead asked for up to
100 pages with `SwissJobHunter/1.0`, which is why the parity sweeps saw 429 and
403: the difference was our request pattern, not the portal.

Sweeping a DECLARED page budget is a complete harvest, not a truncated one.
Without that distinction `last_complete_at` would stay NULL forever and
`harvest.check_health` would raise `cosecha_sin_completar` on every single run
— the chronic red that destroys the signal it exists to give (G9 P2-C).
A budget that is NOT declared keeps the old meaning: hitting the internal
safety cap is still `partial`.
"""

import asyncio

import httpx
import pytest

from jobhunt_core.harvest.provider import ProviderConfigError
from jobhunt_core.harvest.providers.browser_headers import BROWSER_HEADERS
from jobhunt_core.harvest.providers.native_jobgether import JobgetherProvider
from jobhunt_core.harvest.providers.native_nav import NavProvider

FACET = "Kun hjemmekontor"


def _run(provider, params, handler):
    seen = []

    async def main():
        def transport(request):
            seen.append(request)
            return handler(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            return await provider.fetch_new(params, None, http)
    return asyncio.run(main()), seen


def _nav_page(request):
    """Always reports far more hits than the budget can read."""
    offset = int(request.url.params["from"])
    # Real Elasticsearch envelope: identity in _id, payload in _source.
    rows = [{"_id": f"00000000-0000-4000-8000-{offset + i:012d}",
             "_source": {"title": "Utvikler", "employer": {"name": "NAV"},
                         "published": "2026-09-20T08:00:00"}}
            for i in range(100)]
    return httpx.Response(200, json={
        "hits": {"total": {"value": 5000, "relation": "gte"}, "hits": rows}})


def _jobgether_page(request):
    if request.headers.get("User-Agent", "").startswith("SwissJobHunter"):
        return httpx.Response(403)          # the real portal's anti-bot answer
    page = int(request.url.params["page"])
    rows = [{"slug": f"job-{page}-{i}", "title": "Engineer",
             "company": {"name": "Acme"}, "createdAt": "2026-09-20T08:00:00.000Z"}
            for i in range(20)]
    return httpx.Response(200, json={"data": rows, "maxPages": 99})


@pytest.mark.parametrize("provider,params,handler,pages", [
    (NavProvider(), {"remote": FACET, "max_pages": 3}, _nav_page, 3),
    (JobgetherProvider(), {"max_pages": 3}, _jobgether_page, 3),
])
def test_declared_budget_is_swept_completely_and_only_that_far(
        provider, params, handler, pages, monkeypatch):
    for module in ("native_nav", "native_jobgether"):
        monkeypatch.setattr(
            f"jobhunt_core.harvest.providers.{module}.PAGE_PAUSE_S", 0, raising=False)
    result, seen = _run(provider, params, handler)
    assert len(seen) == pages, "the declared budget is also a ceiling"
    assert result.pages_fetched == pages
    assert result.complete is True, "sweeping the declared budget is a COMPLETE harvest"
    assert result.error is None


def test_undeclared_budget_keeps_reporting_the_safety_cap_as_partial(monkeypatch):
    monkeypatch.setattr(
        "jobhunt_core.harvest.providers.native_jobgether.PAGE_PAUSE_S", 0)
    monkeypatch.setattr(
        "jobhunt_core.harvest.providers.native_jobgether.MAX_PAGES", 2)
    result, _ = _run(JobgetherProvider(), {}, _jobgether_page)
    # `page_budget` is this provider's existing way of saying "there was
    # more and we chose not to read it" — preserved, not softened.
    assert result.complete is False and result.error == "page_budget"


@pytest.mark.parametrize("provider,params", [
    (NavProvider(), {"remote": FACET, "max_pages": 0}),
    (NavProvider(), {"remote": FACET, "max_pages": True}),
    (NavProvider(), {"remote": FACET, "max_pages": "3"}),
    (JobgetherProvider(), {"max_pages": -1}),
    (JobgetherProvider(), {"max_pages": 10**9}),
])
def test_invalid_budget_is_a_configuration_error_before_any_request(provider, params):
    with pytest.raises(ProviderConfigError):
        _run(provider, params, _nav_page)


@pytest.mark.parametrize("provider,params,handler", [
    (NavProvider(), {"remote": FACET, "max_pages": 1}, _nav_page),
    (JobgetherProvider(), {"max_pages": 1}, _jobgether_page),
])
def test_requests_carry_the_same_browser_headers_as_the_retiring_producer(
        provider, params, handler, monkeypatch):
    for module in ("native_nav", "native_jobgether"):
        monkeypatch.setattr(
            f"jobhunt_core.harvest.providers.{module}.PAGE_PAUSE_S", 0, raising=False)
    _, seen = _run(provider, params, handler)
    for header, value in BROWSER_HEADERS.items():
        assert seen[0].headers[header] == value


def test_budget_is_operational_and_does_not_reset_the_cursor():
    """Changing how many pages we read must not re-open the admission window."""
    provider = JobgetherProvider()
    assert provider.params_fingerprint({"max_pages": 3}) == \
        provider.params_fingerprint({"max_pages": 9})


def test_portal_duplicate_postings_are_dropped_without_faking_a_failed_sweep(monkeypatch):
    """Jobgether republishes one opening as several postings of the same title.

    Live probe 2026-09-21: 3 ambiguous identities out of 139, 8 listings of 150
    (5,3%). The retiring producer loses them too — it normalizes the slug the
    same way (jobgether.py:130) and the duplicates then collide on ix_jobs_url,
    a chronic incident this project already tracks. So dropping them is parity,
    and refusing to pick a winner stays untouched.

    What must NOT happen is calling that a failed harvest: it recurs on every
    single sweep, `last_complete_at` would never advance and
    `harvest.check_health` would cry `cosecha_sin_completar` forever. The count
    travels in the cursor, where it can be read without poisoning the signal.
    """
    monkeypatch.setattr(
        "jobhunt_core.harvest.providers.native_jobgether.PAGE_PAUSE_S", 0)

    def handler(request):
        page = int(request.url.params["page"])
        rows = [{"slug": f"{'a' * 24}-same-role", "title": "Regulatory Manager",
                 "companyData": {"name": "Acme"}, "createdAt": "2026-09-20T08:00:00.000Z"},
                {"slug": f"{'b' * 24}-same-role", "title": "Regulatory Manager",
                 "companyData": {"name": "Acme"}, "createdAt": "2026-09-20T08:00:00.000Z"},
                {"slug": f"unique-{page}", "title": "Engineer",
                 "companyData": {"name": "Other"}, "createdAt": "2026-09-20T08:00:00.000Z"}]
        return httpx.Response(200, json={"data": rows, "maxPages": 99})

    result, _ = _run(JobgetherProvider(), {"max_pages": 1}, handler)
    assert result.complete is True and result.error is None
    assert result.next_cursor["ambiguous"] == 1
    assert [listing.payload["title"] for listing in result.listings] == ["Engineer"]


def test_a_sweep_that_is_entirely_ambiguous_is_still_an_error():
    """Parity ends where honesty does: nothing usable is not a good harvest."""
    def handler(request):
        rows = [{"slug": f"{c * 24}-same-role", "title": "Regulatory Manager",
                 "companyData": {"name": "Acme"}, "createdAt": "2026-09-20T08:00:00.000Z"}
                for c in ("a", "b")]
        return httpx.Response(200, json={"data": rows, "maxPages": 1})

    with pytest.raises(Exception):
        _run(JobgetherProvider(), {"max_pages": 1}, handler)
