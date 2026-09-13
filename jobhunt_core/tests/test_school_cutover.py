"""Cutover health contract matches Portfolio's actual /health/deep fields."""

import asyncio

import httpx
import pytest

from jobhunt_core import school_cutover
from jobhunt_core.import_schools import SchoolMigrationError


@pytest.mark.parametrize(
    "enabled,allowed", [(False, True), (True, False), (None, False)]
)
def test_portfolio_freeze_requires_actual_scheduler_flag(monkeypatch, enabled, allowed):
    payload = {
        "checks": {
            "schedulers": {
                "writes": "frozen",
                "writes_frozen": True,
                "state": "quiesced",
                "background_schedulers_enabled": enabled,
            }
        }
    }
    client = httpx.AsyncClient
    monkeypatch.setenv("SCHOOL_FREEZE_URL", "https://portfolio.test/health/deep")
    monkeypatch.setattr(
        school_cutover.httpx,
        "AsyncClient",
        lambda **kwargs: client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            )
        ),
    )
    if allowed:
        asyncio.run(school_cutover.require_freeze("portfolio"))
    else:
        with pytest.raises(SchoolMigrationError, match="quiesced"):
            asyncio.run(school_cutover.require_freeze("portfolio"))


def test_reverse_allows_public_catalogue_growth_not_local_school_edits():
    import copy

    original = {
        "jobs": [{"hash": "old", "description": "original"}],
        "match_results": [
            {"id": "state", "application_status": "drafted", "draft_letter": "private"}
        ],
        "user_profiles": [{"user_id": "owner", "watchlist_schools_enabled": True}],
    }
    envelope = {"source": original, "origin": "swissjob"}
    current = copy.deepcopy(original)
    current["jobs"] = [{"hash": "old", "description": "updated"}, {"hash": "new"}]
    current["match_results"].append(
        {"id": "new-state", "application_status": "detected", "draft_letter": None}
    )
    school_cutover._check_source(envelope, current, "reverse")
    with pytest.raises(SchoolMigrationError, match="local school data"):
        school_cutover._check_source(envelope, current, "import")
    for table, field, value in (
        ("match_results", "draft_letter", "unexpected writer"),
        ("match_results", "application_status", "sent"),
        ("user_profiles", "watchlist_schools_enabled", False),
    ):
        altered = copy.deepcopy(current)
        altered[table][0][field] = value
        with pytest.raises(SchoolMigrationError, match="durable changed"):
            school_cutover._check_source(envelope, altered, "reverse")
    current["match_results"][-1]["draft_letter"] = "must not disappear"
    with pytest.raises(SchoolMigrationError, match="unexpected local"):
        school_cutover._check_source(envelope, current, "reverse")
    missing = copy.deepcopy(original)
    missing["match_results"] = []
    with pytest.raises(SchoolMigrationError, match="durable changed"):
        school_cutover._check_source(envelope, missing, "reverse")
    with pytest.raises(SchoolMigrationError, match="local school data"):
        school_cutover._check_source(
            {**envelope, "origin": "portfolio"}, current, "reverse"
        )


def test_open_swiss_school_writer_cannot_be_sealed(monkeypatch):
    client = httpx.AsyncClient
    monkeypatch.setenv("SCHOOL_FREEZE_URL", "https://swiss.test/health/schools")
    monkeypatch.setattr(
        school_cutover.httpx,
        "AsyncClient",
        lambda **kwargs: client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"writes": "enabled"})
            )
        ),
    )
    with pytest.raises(SchoolMigrationError, match="not frozen"):
        asyncio.run(school_cutover.require_freeze("swissjob"))
