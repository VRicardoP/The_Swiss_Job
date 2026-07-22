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
