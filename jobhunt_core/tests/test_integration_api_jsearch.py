"""Authenticated Core seam for ReactPortfolio's JSearch live search."""

from unittest.mock import AsyncMock, patch

from jobhunt_core.tests.test_integration_api import (  # noqa: F401
    _api,
    _issue,
    db,
    pytestmark,
)
from jobhunt_core.tests.test_native_jsearch import row


def test_live_search_requires_scope_and_preserves_filters(db):  # noqa: F811
    factory, made = db
    _cid, _kid, reader = _issue(factory, made, "jsearch-reader", ["vacancies:read"])
    _cid2, _kid2, denied = _issue(factory, made, "jsearch-denied", ["profiles:read"])
    url = (
        "/v1/providers/jsearch/search?query=python&country=GB&page=2&num_pages=3"
        "&date_posted=week&employment_type=CONTRACTOR&remote_only=true"
    )
    assert _api(factory, url, token=denied).status_code == 403

    fetch = AsyncMock(return_value=[row()])
    with patch("jobhunt_core.api.v1_jsearch.search_raw", fetch):
        response = _api(factory, url, token=reader)

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"] == {
        "query": "python",
        "country": "gb",
        "page": 2,
        "results_count": 1,
        "filters": {
            "date_posted": "week",
            "employment_type": "CONTRACTOR",
            "remote_only": True,
        },
    }
    params = fetch.await_args.args[1]
    assert (
        params.query,
        params.country,
        params.page,
        params.num_pages,
        params.date_posted,
        params.employment_type,
        params.remote_only,
    ) == ("python", "gb", 2, 3, "week", "CONTRACTOR", True)


def test_live_search_validates_before_transport(db):  # noqa: F811
    factory, made = db
    _cid, _kid, token = _issue(factory, made, "jsearch-validation", ["vacancies:read"])
    fetch = AsyncMock()
    with patch("jobhunt_core.api.v1_jsearch.search_raw", fetch):
        assert (
            _api(
                factory, "/v1/providers/jsearch/search?query=x", token=token
            ).status_code
            == 400
        )
        assert (
            _api(
                factory,
                "/v1/providers/jsearch/search?query=python&country=invalid",
                token=token,
            ).status_code
            == 400
        )
    fetch.assert_not_awaited()


def test_live_search_sanitizes_provider_failure(db):  # noqa: F811
    from jobhunt_core.harvest.provider import ProviderResponseError

    factory, made = db
    _cid, _kid, token = _issue(factory, made, "jsearch-errors", ["vacancies:read"])
    with patch(
        "jobhunt_core.api.v1_jsearch.search_raw",
        AsyncMock(side_effect=ProviderResponseError("secret request details")),
    ):
        response = _api(
            factory, "/v1/providers/jsearch/search?query=python", token=token
        )
    assert (response.status_code, response.json()["code"]) == (
        503,
        "source_unavailable",
    )
    assert "secret" not in response.text
