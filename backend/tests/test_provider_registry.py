"""Tests for the providers SwissJob still owns."""

from providers import (
    get_all_providers,
    get_provider,
    get_provider_names,
    log_provider_status,
)
from services.job_service import BaseJobProvider

EXPECTED = {
    "careerjet",
    "jooble",
    "jobcloud_partner",
    "linkedin_authorized",
    "indeed_partner",
    "glassdoor_partner",
    "xing_partner",
}


def test_registry_contains_exactly_owned_sources():
    assert set(get_provider_names()) == EXPECTED


def test_unknown_and_missing_key_are_not_constructed():
    assert get_provider("nonexistent") is None
    assert get_provider("proz") is None
    assert get_provider("remoteco") is None


def test_constructed_providers_are_unique_base_providers():
    providers = get_all_providers()
    names = [provider.get_source_name() for provider in providers]
    assert all(isinstance(provider, BaseJobProvider) for provider in providers)
    assert len(names) == len(set(names))
    assert "jobicy" not in names


def test_status_reports_exact_registry_and_key_gates():
    status = log_provider_status()
    assert set(status) == EXPECTED
    assert "disabled" in status["jobcloud_partner"]
