"""SwissJob must not own or construct external job providers."""

from providers import (
    get_all_providers,
    get_provider,
    get_provider_names,
    log_provider_status,
)


def test_registry_is_empty():
    assert get_provider_names() == []


def test_no_provider_is_constructed():
    assert get_provider("nonexistent") is None
    assert get_provider("careerjet") is None
    assert get_provider("jooble") is None
    assert get_provider("proz") is None
    assert get_provider("remoteco") is None


def test_all_providers_is_empty():
    assert get_all_providers() == []


def test_status_is_empty():
    assert log_provider_status() == {}
