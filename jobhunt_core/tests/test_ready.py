"""Sonda /v1/ready (rev. externa #6): estados y NO-fuga de internals."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

import jobhunt_core.api.main as api


def _engine_yielding(version_or_exc):
    """Engine falso cuyo connect() entra en un conn que devuelve la versión o revienta."""
    conn = AsyncMock()
    if isinstance(version_or_exc, Exception):
        conn.execute.side_effect = version_or_exc
    else:
        conn.execute.return_value = SimpleNamespace(scalar=lambda: version_or_exc)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = cm
    return engine


def test_ready_ok(monkeypatch):
    monkeypatch.setattr(api, "engine", _engine_yielding(api._expected_head()))
    r = TestClient(api.app).get("/v1/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_ready_wrong_head_is_503(monkeypatch):
    monkeypatch.setattr(api, "engine", _engine_yielding("deadbeef0000"))
    r = TestClient(api.app).get("/v1/ready")
    assert r.status_code == 503
    assert r.json()["expected"] == api._expected_head()


def test_ready_db_down_is_generic_503(monkeypatch):
    # El texto de la excepción (host/usuario/SQL) NO debe llegar al cliente.
    boom = RuntimeError('connection to server at "postgres" failed for user "jobhunt_core"')
    monkeypatch.setattr(api, "engine", _engine_yielding(boom))
    r = TestClient(api.app).get("/v1/ready")
    assert r.status_code == 503
    body = r.json()
    assert body == {"status": "not_ready", "reason": "database_unavailable"}
    assert "postgres" not in r.text and "jobhunt_core" not in r.text


def test_expected_head_no_se_congela_cuando_la_cadena_crece(monkeypatch, tmp_path):
    """El head esperado NO puede quedar cacheado para toda la vida del proceso.

    El código va montado como volumen: `core-migrate` puede añadir revisiones
    sin que `core-api` reinicie. Con la caché atada al proceso, este servicio
    sirvió dos días un `expected` congelado en el arranque mientras la BD ya
    estaba en un head posterior — 503 con la BD sana.
    """
    llamadas: list[tuple[int, float]] = []

    def _head_falso(huella: tuple[int, float]) -> str:
        llamadas.append(huella)
        return f"head_de_{huella[0]}_ficheros"

    monkeypatch.setattr(api, "_expected_head_para", _head_falso)

    huella_inicial = (3, 100.0)
    monkeypatch.setattr(api, "_versions_fingerprint", lambda: huella_inicial)
    assert api._expected_head() == "head_de_3_ficheros"

    # Llega una revisión nueva al directorio montado, sin reiniciar el proceso.
    monkeypatch.setattr(api, "_versions_fingerprint", lambda: (4, 200.0))
    assert api._expected_head() == "head_de_4_ficheros"

    assert llamadas == [(3, 100.0), (4, 200.0)]


def test_expected_head_reusa_la_cache_con_la_cadena_quieta(monkeypatch):
    """Mientras el directorio no cambia, no se vuelve a parsear la cadena."""
    api._expected_head_para.cache_clear()
    llamadas: list[tuple[int, float]] = []
    real = api._expected_head_para.__wrapped__

    def _contando(huella: tuple[int, float]) -> str:
        llamadas.append(huella)
        return real(huella)

    monkeypatch.setattr(api._expected_head_para, "__wrapped__", _contando, raising=False)
    monkeypatch.setattr(api, "_versions_fingerprint", lambda: (7, 42.0))
    primero = api._expected_head()
    segundo = api._expected_head()
    assert primero == segundo
    assert api._expected_head_para.cache_info().hits >= 1
