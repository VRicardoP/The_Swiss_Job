"""Tests for the provider registry."""

from providers import (
    get_all_providers,
    get_provider,
    get_provider_names,
    log_provider_status,
)
from services.job_service import BaseJobProvider


class TestProviderRegistry:
    def test_get_provider_names_registered(self):
        # Fase 5: 20 providers activos. jobicy/remoteok/himalayas/ictjobs/swisstechjobs
        # están desactivados a propósito (tech-only, bajo valor para el perfil actual).
        names = get_provider_names()
        assert len(names) == 20
        assert "remotive" in names
        assert "jooble" in names
        assert "careerjet" in names
        assert "ostjob" in names
        assert "zebis" in names
        assert "publicjobs" in names
        assert "globaljobs" in names

    def test_get_provider_known(self):
        provider = get_provider("remotive")
        assert provider is not None
        assert isinstance(provider, BaseJobProvider)
        assert provider.get_source_name() == "remotive"

    def test_get_provider_unknown_returns_none(self):
        assert get_provider("nonexistent") is None

    def test_get_provider_without_key_returns_none(self):
        # jsearch requires JSEARCH_RAPIDAPI_KEY which is empty by default
        provider = get_provider("jsearch")
        assert provider is None

    def test_get_all_providers_returns_no_key_providers(self):
        """Should return at least the 10 providers that don't need API keys."""
        providers = get_all_providers()
        names = [p.get_source_name() for p in providers]
        assert len(providers) >= 10
        # These should always be present (no key required)
        assert "remotive" in names
        assert "arbeitnow" in names
        assert "weworkremotely" in names
        assert "workingnomads" in names
        assert "ostjob" in names
        assert "zentraljob" in names
        assert "publicjobs" in names
        assert "zebis" in names
        assert "globaljobs" in names
        assert "proz" in names

    def test_all_providers_are_base_job_provider(self):
        for provider in get_all_providers():
            assert isinstance(provider, BaseJobProvider)

    def test_all_providers_have_unique_source_names(self):
        providers = get_all_providers()
        names = [p.get_source_name() for p in providers]
        assert len(names) == len(set(names))

    def test_log_provider_status_returns_all_providers(self):
        status = log_provider_status()
        assert len(status) == 20
        # Free providers should be enabled
        assert status["remotive"] == "enabled"
        assert status["arbeitnow"] == "enabled"
        # Key-gated providers should show disabled reason
        assert "disabled" in status.get("jsearch", "")
