"""A duplicate dispatch must not repeat an already completed public fetch."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from jobhunt_core.harvest.provider import BaseProvider
from jobhunt_core.harvest.types import FetchResult
from jobhunt_core.tasks import harvest
from jobhunt_core.tests.test_integration_runs import db, _seed_scopes, pytestmark  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)


def test_same_window_only_fetches_once_and_disabled_scope_is_not_dispatched(
    db,  # noqa: F811  (la fixture, no una redefinición)
    monkeypatch,
):
    factory, created = db
    enabled, disabled = _seed_scopes(factory, created, n=2)
    calls = []

    @asynccontextmanager
    async def sessions():
        yield factory

    class Provider(BaseProvider):
        name = "arbeitnow"

        async def fetch_new(self, params, cursor, http):
            calls.append(params)
            return FetchResult((), {})

    monkeypatch.setattr(harvest, "task_session_factory", sessions)
    monkeypatch.setattr(harvest, "get_provider", lambda _: Provider())

    async def check():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE harvest_scopes SET enabled=false WHERE id=:sid"),
                {"sid": disabled},
            )
            await s.commit()
        selected = await harvest._enabled_native_scope_ids()
        assert str(enabled) in selected and str(disabled) not in selected
        try:
            first = await harvest._run_scope_impl(
                str(enabled), run_key="native:window-1"
            )
            assert first.status == "ok"
            duplicate = await harvest._run_scope_impl(
                str(enabled), run_key="native:window-1"
            )
            assert duplicate.status == "skipped"
            assert len(calls) == 1
            next_window = await harvest._run_scope_impl(
                str(enabled), run_key="native:window-2"
            )
            assert next_window.status == "ok"
            assert len(calls) == 2
        finally:
            async with factory() as s:
                ids = (
                    (
                        await s.execute(
                            sa.text(
                                "SELECT run_id FROM source_harvest_runs WHERE scope_id=:sid"
                            ),
                            {"sid": enabled},
                        )
                    )
                    .scalars()
                    .all()
                )
                created["runs"].extend(ids)

    asyncio.run(check())


class _RelojFijo:
    """`datetime` de mentira: `now(tz)` devuelve la hora que se le clave."""

    def __init__(self, momento):
        self._momento = momento

    def now(self, tz=None):
        return self._momento.astimezone(tz) if tz else self._momento


def _ventana_a(momento, monkeypatch) -> str:
    monkeypatch.setattr(harvest, "datetime", _RelojFijo(momento))
    return harvest.current_window()


def test_la_ventana_se_ancla_al_slot_de_zurich_no_al_de_utc(monkeypatch):
    """H8/T11: el beat dispara con `timezone="Europe/Zurich"`; la etiqueta se
    truncaba en UTC. En verano son dos horas de desfase, así que la etiqueta no
    correspondía al disparo y en el cambio de hora dos disparos distintos podían
    caer en la misma ventana —perdiendo una cosecha entera por idempotencia—.

    Nota sobre el acta (T11 §2): su ejemplo dice «12:59 y 13:00 dan ventanas
    distintas». Con slots de SEIS horas ambas caen en el de las 12:00; el límite
    real es 11:59 → 12:00, que es el que se comprueba aquí.
    """
    zurich = ZoneInfo("Europe/Zurich")

    antes = _ventana_a(datetime(2026, 7, 15, 11, 59, tzinfo=zurich), monkeypatch)
    despues = _ventana_a(datetime(2026, 7, 15, 12, 0, tzinfo=zurich), monkeypatch)
    assert antes != despues
    assert antes.startswith("2026-07-15T06:00:00")
    assert despues.startswith("2026-07-15T12:00:00")

    # Idempotencia: dos disparos separados 50 min DENTRO del slot dan la misma
    # etiqueta, que es la clave del run (`native:<window>:<scope>`).
    assert _ventana_a(
        datetime(2026, 7, 15, 12, 10, tzinfo=zurich), monkeypatch
    ) == _ventana_a(datetime(2026, 7, 15, 13, 0, tzinfo=zurich), monkeypatch)

    # Y la prueba de que es Zurich y no UTC: 00:30 en Zurich es el día ANTERIOR
    # a las 22:30 UTC. Anclado en UTC la etiqueta sería la de las 18:00 del 14.
    medianoche = _ventana_a(datetime(2026, 7, 16, 0, 30, tzinfo=zurich), monkeypatch)
    assert medianoche.startswith("2026-07-16T00:00:00")
