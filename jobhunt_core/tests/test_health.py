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
