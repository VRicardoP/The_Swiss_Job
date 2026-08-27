"""A-01: la API /v1 arranca y responde health."""

from fastapi.testclient import TestClient

import jobhunt_core
from jobhunt_core.api.main import app


def test_health_ok():
    client = TestClient(app)
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "jobhunt-core"
    assert body["version"]


def test_health_publica_release_y_head_esperado():
    """P1-3 (auditoría externa 2026-08-27): `version` es la constante 0.1.0 y no distingue
    releases. La sonda publica además el SHA de la imagen y el head de migraciones que
    ESTE proceso espera — con eso se comprueba, tras un despliegue, que API, worker y
    capturador corren la misma release."""
    body = TestClient(app).get("/v1/health").json()
    assert body["release"] == jobhunt_core.__release_sha__
    assert body["alembic_expected"]


def test_health_declara_si_el_proceso_es_autoritativo(monkeypatch):
    """REGRESIÓN auditoría G9 P2-A: `/v1/health` emparejaba dos datos de PROCEDENCIA
    DISTINTA sin decirlo — `release` sale del ENV horneado en la imagen y
    `alembic_expected` se lee del sistema de ficheros (en el perfil de desarrollo, el
    árbol montado). La marca `authoritative` solo existía en `/v1/ready`, y health es
    justo la sonda que el ritual de verificación de despliegue ejecuta primero: publicaba
    el SHA de una imagen mientras corría código que demostrablemente no era ese SHA.
    """
    import jobhunt_core.api.main as api

    monkeypatch.setattr(api, "__release_sha__", "abc1234")
    monkeypatch.setattr(api, "_BAKED_RELEASE", "abc1234")  # la que hornea la imagen (G10 P2-2)
    monkeypatch.setattr(api, "CODE_MUTABLE", False)
    assert TestClient(app).get("/v1/health").json()["authoritative"] is True
    monkeypatch.setattr(api, "CODE_MUTABLE", True)
    assert TestClient(app).get("/v1/health").json()["authoritative"] is False
