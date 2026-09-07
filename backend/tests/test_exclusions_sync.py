"""Proyección de filtros del BFF al core (revisión externa 2026-09-07, B).

El defecto: el BFF escribía sus altas/bajas en la tabla legacy y solo un
importador de ALTAS llegaba al core, así que una baja no se proyectaba nunca
y la regla seguía excluyendo. Aquí se fija el contrato declarativo.
"""

import uuid

import pytest

from services import exclusions_sync


class _Resp:
    status_code = 200
    text = ""


class _Client:
    def __init__(self, capturado):
        self._capturado = capturado

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def put(self, url, json=None):
        self._capturado.append((url, json))
        return _Resp()


@pytest.mark.asyncio
async def test_no_proyecta_si_el_perfil_sigue_en_local(monkeypatch):
    monkeypatch.setattr(exclusions_sync, "resolve_mode",
                        _corutina("local"))
    r = await exclusions_sync.sync_exclusions_to_core(None, uuid.uuid4())
    assert r["status"] == "local"


@pytest.mark.asyncio
async def test_proyecta_el_conjunto_COMPLETO_incluida_una_baja(monkeypatch):
    """Tras desactivar un filtro, el conjunto empujado NO lo contiene: así es
    como una BAJA llega al core sin necesitar una operación propia."""
    capturado = []
    pid = uuid.uuid4()
    monkeypatch.setattr(exclusions_sync, "resolve_mode",
                        _corutina("core_primary"))
    monkeypatch.setattr(exclusions_sync, "resolve_core_profile_id",
                        _corutina(pid))

    activos = [
        _Filtro("title_contains", "Director"),
        _Filtro("tag_contains", "VP"),
    ]
    db = _Db(activos)
    r = await exclusions_sync.sync_exclusions_to_core(
        db, uuid.uuid4(), client_factory=lambda: _Client(capturado))
    assert r == {"status": "ok", "declaradas": 2}
    url, payload = capturado[-1]
    assert url == f"/profiles/{pid}/exclusions"
    assert payload["exclusions"] == [
        {"kind": "title_contains", "pattern": "Director"},
        {"kind": "tag_contains", "pattern": "VP"},
    ]

    # BAJA: el usuario desactiva uno ⇒ el conjunto empujado tiene UNO
    activos.pop()
    r2 = await exclusions_sync.sync_exclusions_to_core(
        db, uuid.uuid4(), client_factory=lambda: _Client(capturado))
    assert r2["declaradas"] == 1
    assert capturado[-1][1]["exclusions"] == [
        {"kind": "title_contains", "pattern": "Director"}]


@pytest.mark.asyncio
async def test_un_core_caido_no_tumba_la_operacion_del_usuario(monkeypatch):
    monkeypatch.setattr(exclusions_sync, "resolve_mode",
                        _corutina("core_primary"))
    monkeypatch.setattr(exclusions_sync, "resolve_core_profile_id",
                        _corutina(uuid.uuid4()))

    def _revienta():
        raise RuntimeError("core caido")

    r = await exclusions_sync.sync_exclusions_to_core(
        _Db([]), uuid.uuid4(), client_factory=_revienta)
    assert r["status"] == "core_inaccesible"


def _corutina(valor):
    async def _f(*args, **kwargs):
        return valor

    return _f


class _Filtro:
    def __init__(self, tipo, patron):
        self.filter_type = tipo
        self.pattern = patron


class _Scalars:
    def __init__(self, filas):
        self._filas = filas

    def all(self):
        return list(self._filas)


class _Result:
    def __init__(self, filas):
        self._filas = filas

    def scalars(self):
        return _Scalars(self._filas)


class _Db:
    def __init__(self, filas):
        self._filas = filas

    async def execute(self, *a, **k):
        return _Result(self._filas)
