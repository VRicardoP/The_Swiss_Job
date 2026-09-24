"""Sondas externas del envío declarativo; montar bajo backend/tests."""

import asyncio
import uuid

import pytest

from services import exclusions_sync as sync
from tests.test_exclusions_sync import _Db, _Filtro, _Resp, _corutina


@pytest.mark.asyncio
async def test_delayed_snapshot_must_not_resurrect_deleted_filter(monkeypatch):
    monkeypatch.setattr(sync, "resolve_mode", _corutina("core_primary"))
    monkeypatch.setattr(sync, "resolve_core_profile_id", _corutina(uuid.uuid4()))
    entered, release = asyncio.Event(), asyncio.Event()
    core = []
    active = [_Filtro("title_contains", "Director")]
    calls = 0

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def put(self, url, json=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            # Contrato real del PUT: sustitución atómica del conjunto recibido.
            core[:] = json["exclusions"]
            return _Resp()

    uid = uuid.uuid4()
    a = asyncio.create_task(sync.sync_exclusions_to_core(_Db(active), uid, Client))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        active.clear()  # otra petición confirma la BAJA en el BFF
        b = await sync.sync_exclusions_to_core(_Db(active), uid, Client)
        assert b["status"] == "ok" and core == []
    finally:
        release.set()
        await a
    assert core == [], f"La petición vieja RESTAURA la regla borrada: {core}"


@pytest.mark.asyncio
async def test_failed_delivery_must_not_be_silently_acknowledged(monkeypatch):
    # Esta prueba documenta el contrato ACTUAL, no espera una solución futura:
    # la entrega falla y el llamador recibe solo un estado que el router ignora.
    monkeypatch.setattr(sync, "resolve_mode", _corutina("core_primary"))
    monkeypatch.setattr(sync, "resolve_core_profile_id", _corutina(uuid.uuid4()))

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def put(self, *args, **kwargs):
            raise ConnectionError("corte controlado")

    result = await sync.sync_exclusions_to_core(_Db([]), uuid.uuid4(), Client)
    assert result == {"status": "core_inaccesible"}
