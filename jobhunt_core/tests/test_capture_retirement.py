"""Retiring the CDC slot must silence its harness without silencing native work.

Once the legacy producers are gone there is nothing left to capture, so the
tasks that exist only to watch the replication slot and to close the shadow
cycle stop making sense. Native matching recovery and delivery health remain.

So the flag is narrow on purpose, defaults to the current behaviour, and these
tests pin exactly which entries it may remove.
"""

import importlib

import pytest

SLOT_ONLY = {"shadow-check-slot-health", "shadow-preview-cycle", "shadow-run-cycle"}
# Everything below survives: it either feeds the native cycle or bounds growth.
ALWAYS = {
    "harvest-dispatch-native",
    "harvest-check-health",
    "matching-recovery",
    "delivery-check-health",
    "delivery-dispatch-outbox",
    "idempotency-purge-expired",
    "maintenance-archive-sweep",
    "maintenance-dedup-scan",
    "maintenance-purge-retention",
    "matching-materialize-ce",
}


def _schedule(monkeypatch, capture_enabled):
    from jobhunt_core import config

    monkeypatch.setattr(
        config.settings, "CORE_CAPTURE_ENABLED", capture_enabled, raising=False
    )
    module = importlib.reload(importlib.import_module("jobhunt_core.celery_app"))
    return set(module.celery_app.conf.beat_schedule)


def test_capture_enabled_is_the_default_and_changes_nothing(monkeypatch):
    from jobhunt_core.config import settings

    assert settings.CORE_CAPTURE_ENABLED is True, "the flag must be opt-OUT"
    entries = _schedule(monkeypatch, True)
    assert SLOT_ONLY <= entries and ALWAYS <= entries


def test_disabling_capture_removes_only_the_slot_harness(monkeypatch):
    entries = _schedule(monkeypatch, False)
    assert not (SLOT_ONLY & entries), "the slot harness must be gone"
    assert ALWAYS <= entries, "the native cycle must survive untouched"


def test_the_matching_engine_is_never_switched_off_by_the_flag(monkeypatch):
    for enabled in (True, False):
        assert "matching-recovery" in _schedule(monkeypatch, enabled)


@pytest.fixture(autouse=True)
def _restore_module():
    """Reload with the REAL setting, not with monkeypatch's value.

    monkeypatch undoes itself after this fixture, so reloading here without
    restoring first would leave the module cached with the flag off and quietly
    strip those beat entries for every later test in the session.
    """
    from jobhunt_core import config

    original = getattr(config.settings, "CORE_CAPTURE_ENABLED", True)
    yield
    config.settings.CORE_CAPTURE_ENABLED = original
    importlib.reload(importlib.import_module("jobhunt_core.celery_app"))
