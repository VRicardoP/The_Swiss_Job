"""D2 — piezas puras de la fusión y de su programación (sin base de datos)."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from jobhunt_core import dedup_merge
from jobhunt_core.dedup_merge import VacancyProfile, choose_winner, exact_rule_holds
from jobhunt_core.tasks import maintenance

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _v(n, *, native=True, live=True, desc="d", created=T0, **content):
    return VacancyProfile(
        id=uuid.UUID(int=n),
        created_at=created,
        text_hash="h",
        content={"description": desc, "location": "Zürich", **content},
        live=live,
        native_live=native and live,
    )


@pytest.mark.parametrize(
    "a, b, ganador",
    [
        # nativa viva gana aunque la legacy sea más antigua y más completa
        (_v(1, native=False, desc="x" * 99, created=T0 - timedelta(days=9)), _v(2), 2),
        # con encarnación activa gana a sin ella
        (_v(1, native=False, live=False, desc="x" * 99), _v(2, native=False), 2),
        # descripción más larga
        (_v(1), _v(2, desc="dd"), 2),
        # más antigua
        (_v(1, created=T0 + timedelta(days=1)), _v(2), 2),
        # id menor como último desempate
        (_v(2), _v(1), 1),
    ],
)
def test_choose_winner_is_deterministic_and_symmetric(a, b, ganador):
    w1, l1 = choose_winner(a, b)
    w2, l2 = choose_winner(b, a)  # control: el orden de entrada no decide
    assert w1.id == w2.id == uuid.UUID(int=ganador)
    assert l1.id == l2.id != w1.id


def test_exact_rule():
    base = _v(1)
    assert exact_rule_holds(base, _v(2))  # control: idénticas
    assert exact_rule_holds(base, _v(2, salary="CHF 1"))  # un lado sin dato no veta
    assert not exact_rule_holds(_v(1, salary="CHF 1"), _v(2, salary="CHF 2"))
    assert not exact_rule_holds(_v(1, remote=True), _v(2, remote=False))
    assert not exact_rule_holds(base, _v(2, location="Basel"))
    other = VacancyProfile(**{**_v(2).__dict__, "text_hash": "otro"})
    assert not exact_rule_holds(base, other)


def test_merged_pvs_keeps_the_strongest():
    t1, t2 = T0, T0 + timedelta(hours=1)
    loser = {
        "feedback": "thumbs_down",
        "feedback_recorded_at": t2,
        "dismissed_at": None,
        "saved_at": t1,
        "notes": "del perdedor",
    }
    winner = {
        "feedback": "thumbs_up",
        "feedback_recorded_at": t1,
        "dismissed_at": None,
        "saved_at": None,
        "notes": "del ganador",
    }
    out = dedup_merge._merged_pvs(winner, loser)
    assert out["feedback"] == "thumbs_down"  # el feedback más reciente
    assert out["saved_at"] == t1  # el ganador no tenía: se hereda
    assert out["notes"] == "del ganador"  # el ganador lo tenía: se conserva
    # Control: con el feedback del ganador más reciente, se queda el suyo.
    out = dedup_merge._merged_pvs(
        {**winner, "feedback_recorded_at": t2 + t2.resolution}, loser
    )
    assert out["feedback"] == "thumbs_up"
    assert dedup_merge._merged_pvs(None, loser)["notes"] == "del perdedor"


def _zurich(h, m):
    return datetime(2026, 11, 26, h, m, tzinfo=ZoneInfo("Europe/Zurich"))


def test_nightly_deadline():
    assert maintenance.nightly_deadline(_zurich(0, 30)) == _zurich(1, 20)
    # A mano a media tarde: una hora, no hasta las 01:20 del día siguiente.
    assert maintenance.nightly_deadline(_zurich(14, 0)) == _zurich(15, 0)


def test_nightly_waits_while_harvest_runs(monkeypatch):
    async def busy():
        return True

    monkeypatch.setattr(maintenance, "_harvest_busy", busy)
    deadline = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with (
        patch.object(maintenance.dedup_nightly_task, "apply_async") as again,
        patch.object(maintenance.dedup_scan_task, "apply_async") as scan,
    ):
        result = maintenance.dedup_nightly_task.run(deadline=deadline)
    assert result["status"] == "esperando_cosecha"
    again.assert_called_once_with(
        kwargs={"deadline": datetime.fromisoformat(deadline).isoformat()},
        countdown=maintenance.DEDUP_HARVEST_POLL_S,
    )
    scan.assert_not_called()


def test_nightly_gives_up_at_the_deadline(monkeypatch):
    async def busy():
        return True

    monkeypatch.setattr(maintenance, "_harvest_busy", busy)
    deadline = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    with (
        patch.object(maintenance.dedup_nightly_task, "apply_async") as again,
        patch.object(maintenance.dedup_scan_task, "apply_async") as scan,
    ):
        result = maintenance.dedup_nightly_task.run(deadline=deadline)
    assert result["status"] == "saltada_cosecha_en_curso"
    again.assert_not_called()
    scan.assert_not_called()


def test_nightly_starts_scan_when_harvest_is_done(monkeypatch):
    async def idle():
        return False

    monkeypatch.setattr(maintenance, "_harvest_busy", idle)
    deadline = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with (
        patch.object(maintenance.dedup_nightly_task, "apply_async") as again,
        patch.object(maintenance.dedup_scan_task, "apply_async") as scan,
    ):
        result = maintenance.dedup_nightly_task.run(deadline=deadline)
    assert result["status"] == "escaneo_encolado"
    again.assert_not_called()
    scan.assert_called_once_with(
        kwargs={"merge_deadline": datetime.fromisoformat(deadline).isoformat()}
    )


def test_harvest_busy_counts_queued_scopes(monkeypatch):
    """Un scope encolado aún no tiene fila en source_harvest_runs."""
    import asyncio

    monkeypatch.setattr(maintenance, "harvest_queue_depth", lambda: 3)
    assert asyncio.run(maintenance._harvest_busy()) is True


def test_final_scan_fragment_enqueues_merge(monkeypatch):
    async def fragment(*args):
        return {
            "status": "ok",
            "complete": True,
            "next_cursor": None,
            "window_start": "2026-10-09T04:00:00+00:00",
            "window_end": "2026-10-10T06:00:00+00:00",
        }

    monkeypatch.setattr(maintenance, "_run_dedup_fragment", fragment)
    with patch.object(maintenance.dedup_merge_task, "apply_async") as merge:
        result = maintenance.dedup_scan_task.run(
            merge_deadline="2026-10-10T23:20:00+00:00"
        )
        # Control: sin merge_deadline (lanzamiento a mano) no se fusiona.
        maintenance.dedup_scan_task.run()
    assert result["merge_enqueued"] is True
    merge.assert_called_once_with(
        kwargs={"deadline": "2026-10-10T23:20:00+00:00"}, countdown=1
    )


def test_merge_task_continues_only_before_the_deadline(monkeypatch):
    async def partial(deadline, after_id):
        return {"merged": 5, "complete": False, "next_after": str(uuid.UUID(int=7))}

    monkeypatch.setattr(maintenance, "_run_merge_fragment", partial)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with patch.object(maintenance.dedup_merge_task, "apply_async") as again:
        result = maintenance.dedup_merge_task.run(deadline=future)
        late = maintenance.dedup_merge_task.run(deadline=past)
    assert result["continuation_enqueued"] is True
    again.assert_called_once_with(
        kwargs={"deadline": future, "after_id": str(uuid.UUID(int=7))}, countdown=1
    )
    assert late["status"] == "fuera_de_hora"


def test_merge_task_kill_switch(monkeypatch):
    called = []

    async def fragment(deadline, after_id):
        called.append(1)
        return {"complete": True, "next_after": None}

    monkeypatch.setattr(maintenance, "_run_merge_fragment", fragment)
    monkeypatch.setattr(maintenance.settings, "CORE_DEDUP_MERGE_ENABLED", False)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert (
        maintenance.dedup_merge_task.run(deadline=future)["status"] == "deshabilitada"
    )
    assert called == []
    monkeypatch.setattr(maintenance.settings, "CORE_DEDUP_MERGE_ENABLED", True)
    maintenance.dedup_merge_task.run(deadline=future)  # control: encendida, corre
    assert called == [1]


def test_beat_replaces_the_0520_slot():
    from celery.schedules import crontab

    from jobhunt_core.celery_app import celery_app

    beat = celery_app.conf.beat_schedule
    assert "maintenance-dedup-scan" not in beat
    entry = beat["maintenance-dedup-nightly"]
    assert entry["task"] == "jobhunt.maintenance.dedup_nightly"
    assert entry["schedule"] == crontab(hour=0, minute=30)
    assert celery_app.conf.task_routes["jobhunt.maintenance.dedup_nightly"] == {
        "queue": "core.default"
    }
