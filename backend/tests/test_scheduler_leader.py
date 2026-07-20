"""Tests del leader-lock del scheduler y de configure_logging.

El scheduler debe arrancar en UN SOLO proceso (evita doble disparo con varios
workers de gunicorn). Se prueba la lógica de elección (`_leader_step`) sin el
bucle infinito.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

from services import scheduler as sched_mod


async def test_leader_step_acquires_and_starts_scheduler(monkeypatch):
    fake_setup = MagicMock()
    fake_scheduler = MagicMock(running=False)
    monkeypatch.setattr(sched_mod, "setup_schedules", fake_setup)
    monkeypatch.setattr(sched_mod, "scheduler", fake_scheduler)

    r = MagicMock()
    r.set = AsyncMock(return_value=True)  # ganamos el lock

    result = await sched_mod._leader_step(r, is_leader=False)

    assert result is True
    fake_setup.assert_called_once()
    fake_scheduler.start.assert_called_once()
    r.set.assert_awaited_once()


async def test_leader_step_not_acquired_does_not_start(monkeypatch):
    fake_setup = MagicMock()
    fake_scheduler = MagicMock(running=False)
    monkeypatch.setattr(sched_mod, "setup_schedules", fake_setup)
    monkeypatch.setattr(sched_mod, "scheduler", fake_scheduler)

    r = MagicMock()
    r.set = AsyncMock(return_value=None)  # otro proceso ya es líder

    result = await sched_mod._leader_step(r, is_leader=False)

    assert result is False
    fake_setup.assert_not_called()
    fake_scheduler.start.assert_not_called()


async def test_leader_step_renews_when_still_leader(monkeypatch):
    fake_scheduler = MagicMock(running=True)
    monkeypatch.setattr(sched_mod, "scheduler", fake_scheduler)

    r = MagicMock()
    r.get = AsyncMock(return_value=sched_mod._WORKER_ID)  # el lock sigue siendo nuestro
    r.expire = AsyncMock()

    result = await sched_mod._leader_step(r, is_leader=True)

    assert result is True
    r.expire.assert_awaited_once()
    fake_scheduler.shutdown.assert_not_called()


async def test_leader_step_steps_down_when_lock_lost(monkeypatch):
    fake_scheduler = MagicMock(running=True)
    monkeypatch.setattr(sched_mod, "scheduler", fake_scheduler)

    r = MagicMock()
    r.get = AsyncMock(return_value="otro-worker:999")  # perdimos el lock

    result = await sched_mod._leader_step(r, is_leader=True)

    assert result is False
    fake_scheduler.shutdown.assert_called_once()


def test_configure_logging_sets_info_level(monkeypatch):
    from config import settings
    from logging_setup import configure_logging

    monkeypatch.setattr(settings, "LOG_LEVEL", "INFO")
    configure_logging()

    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("services").level == logging.INFO
    assert logging.getLogger("tasks").level == logging.INFO
