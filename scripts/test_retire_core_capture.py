from pathlib import Path

import pytest

from scripts.retire_core_capture import (
    CORE_SERVICES,
    disable_capture_schedule,
    finalize_capture_removal,
    transform,
)


COMPOSE = Path("deploy/nas/core.configured.yml")


def test_disable_is_narrow_and_idempotent(tmp_path):
    original = COMPOSE.read_text()
    compose = tmp_path / "core.yml"
    compose.write_text(original)

    assert transform(compose, "disable") is True
    disabled = compose.read_text()
    assert disabled.count("CORE_CAPTURE_ENABLED: 'false'") == 1
    assert disabled.index("  core-ops-worker:") < disabled.index(
        "CORE_CAPTURE_ENABLED: 'false'"
    ) < disabled.index("  core-capture:")
    assert disabled.count("CORE_CAPTURE_SLOT:") == 5
    assert transform(compose, "disable") is False


def test_finalize_requires_disabled_schedule_and_is_idempotent(tmp_path):
    original = COMPOSE.read_text()
    with pytest.raises(ValueError, match="debe estar false"):
        finalize_capture_removal(original)

    compose = tmp_path / "core.yml"
    compose.write_text(disable_capture_schedule(original))
    assert transform(compose, "finalize") is True
    final = compose.read_text()
    assert "  core-capture:" not in final
    assert "CORE_CAPTURE_SLOT:" not in final
    assert "\nnetworks:\n" in final
    assert "\nvolumes:\n" in final
    assert final.count("CORE_CAPTURE_ENABLED: 'false'") == 1
    assert all(f"  {service}:" in final for service in CORE_SERVICES)
    assert transform(compose, "finalize") is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.replace(
            "CORE_CAPTURE_SLOT: jobhunt_shadow_r5_rehearsal", "OTHER: value", 1
        ),
        lambda data: data.replace(
            "  core-capture:\n", "  renamed-capture:\n", 1
        ),
        lambda data: data.replace(
            "CORE_CAPTURE_ENABLED: 'false'", "CORE_CAPTURE_ENABLED: 'true'"
        ),
    ],
)
def test_rejects_drift_instead_of_guessing(mutation):
    disabled = disable_capture_schedule(COMPOSE.read_text())
    with pytest.raises(ValueError):
        finalize_capture_removal(mutation(disabled))
