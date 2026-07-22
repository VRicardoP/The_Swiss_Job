"""A-01: la API /v1 arranca y responde health."""

from fastapi.testclient import TestClient

from jobhunt_core.api.main import app


def test_health_ok():
    client = TestClient(app)
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "jobhunt-core"
    assert body["version"]
