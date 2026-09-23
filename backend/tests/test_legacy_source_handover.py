"""A source handed to core cannot restart through the legacy registry.

Startup configuration is not an in-flight cancellation: deployment must drain
old workers before it activates the corresponding native core scopes.
"""

from unittest.mock import Mock

import pytest

import providers
import scrapers
from config import settings


@pytest.fixture(autouse=True)
def require_handover_fields():
    # Avoid pytest rendering Settings (including credentials) on an old parent.
    assert {"LEGACY_DISABLED_PROVIDERS", "LEGACY_DISABLED_SCRAPERS"} <= set(
        type(settings).model_fields
    ), "Startup source handover fields are missing"


@pytest.mark.parametrize(
    "registry,field,classes,single,all_names",
    [
        (
            providers,
            "LEGACY_DISABLED_PROVIDERS",
            "_PROVIDER_CLASSES",
            "get_provider",
            "get_all_providers",
        ),
        (
            scrapers,
            "LEGACY_DISABLED_SCRAPERS",
            "_SCRAPER_CLASSES",
            "get_scraper",
            "get_all_scrapers",
        ),
    ],
)
def test_disabled_source_never_constructed(
    monkeypatch, registry, field, classes, single, all_names
):
    disabled, enabled = Mock(), Mock()
    monkeypatch.setattr(registry, classes, {"retired": disabled, "live": enabled})
    monkeypatch.setattr(settings, field, ["retired"])
    assert getattr(registry, single)("retired") is None
    assert getattr(registry, single)("live") is enabled.return_value
    assert getattr(registry, all_names)() == [enabled.return_value]
    disabled.assert_not_called()


@pytest.mark.parametrize(
    "registry,field,classes,single,all_names",
    [
        (
            providers,
            "LEGACY_DISABLED_PROVIDERS",
            "_PROVIDER_CLASSES",
            "get_provider",
            "get_all_providers",
        ),
        (
            scrapers,
            "LEGACY_DISABLED_SCRAPERS",
            "_SCRAPER_CLASSES",
            "get_scraper",
            "get_all_scrapers",
        ),
    ],
)
def test_misspelled_handover_fails_before_any_constructor(
    monkeypatch, registry, field, classes, single, all_names
):
    constructor = Mock()
    monkeypatch.setattr(registry, classes, {"retired": constructor})
    monkeypatch.setattr(settings, field, ["retierd"])
    with pytest.raises(ValueError, match="Unknown disabled legacy sources"):
        getattr(registry, all_names)()
    with pytest.raises(ValueError, match="Unknown disabled legacy sources"):
        getattr(registry, single)("retired")
    constructor.assert_not_called()


def test_handover_observable_without_hiding_catalogue(monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_DISABLED_PROVIDERS", ["remotive"])
    assert providers.log_provider_status()["remotive"] == "disabled (core handover)"
    assert "remotive" in providers.get_provider_names()


def test_disabling_does_not_enable_restricted_sources(monkeypatch):
    monkeypatch.setattr(settings, "LEGACY_DISABLED_PROVIDERS", ["remotive"])
    monkeypatch.setattr(settings, "JOBCLOUD_PARTNER_API_KEY", "")
    assert providers.get_provider("jobcloud_partner") is None
    assert all(
        p.get_source_name() != "jobcloud_partner" for p in providers.get_all_providers()
    )


def test_empty_default_preserves_enabled_sources():
    assert settings.LEGACY_DISABLED_PROVIDERS == []
    assert settings.LEGACY_DISABLED_SCRAPERS == []
    assert providers.get_provider("remotive") is not None
    assert scrapers.get_scraper("swiss_schools_iscs") is not None


def test_legacy_health_does_not_warn_about_handed_over_scrapers(monkeypatch):
    from tasks.watchlist_tasks import _get_watchlist_sources

    monkeypatch.setattr(settings, "LEGACY_DISABLED_SCRAPERS", ["swiss_schools_iscs"])
    assert "swiss_schools_iscs" not in _get_watchlist_sources()
    assert "swiss_schools_zis" in _get_watchlist_sources()
    assert "swiss_schools_iscs" in scrapers.get_scraper_names()


@pytest.mark.parametrize(
    "field", ["LEGACY_DISABLED_PROVIDERS", "LEGACY_DISABLED_SCRAPERS"]
)
async def test_invalid_handover_aborts_startup_before_io(monkeypatch, field):
    import main

    monkeypatch.setattr(settings, field, ["misspelled-source"])
    no_io = Mock(side_effect=AssertionError("startup I/O before handover validation"))
    monkeypatch.setattr(main.aioredis, "from_url", no_io)
    with pytest.raises(ValueError, match="Unknown disabled legacy sources"):
        async with main.lifespan(main.app):
            pytest.fail("invalid configuration started")
    no_io.assert_not_called()
