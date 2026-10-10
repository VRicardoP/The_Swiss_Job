import pytest

from scripts.set_match_facets_flag import set_enabled


def test_sets_flag_in_both_directions_and_is_idempotent(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  backend:\n    environment:\n"
        "      CORE_MATCH_FACETS_ENABLED: 'false'\n"
    )
    assert set_enabled(compose, True) is True
    assert set_enabled(compose, True) is False
    assert "CORE_MATCH_FACETS_ENABLED: 'true'" in compose.read_text()
    assert set_enabled(compose, False) is True


def test_inserts_missing_flag_in_backend_environment(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  backend:\n    environment:\n      OTHER: value\n"
        "  worker:\n    environment: {}\n"
    )
    assert set_enabled(compose, True) is True
    assert compose.read_text().count("CORE_MATCH_FACETS_ENABLED: 'true'") == 1


def test_rejects_missing_backend_or_ambiguous_flag(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    with pytest.raises(ValueError):
        set_enabled(compose, True)
    compose.write_text(
        "services:\n  backend:\n    environment:\n"
        "      CORE_MATCH_FACETS_ENABLED: 'false'\n"
        "      CORE_MATCH_FACETS_ENABLED: 'true'\n"
    )
    with pytest.raises(ValueError):
        set_enabled(compose, True)
