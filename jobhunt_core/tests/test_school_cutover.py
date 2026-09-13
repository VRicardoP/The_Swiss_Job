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
