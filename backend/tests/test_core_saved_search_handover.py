"""The saved-search seam preserves the UI and switches BOTH executor entrypoints."""

import uuid

import httpx
import pytest
from sqlalchemy import func, select

from config import settings
from models.jobhunt_profile_map import JobhuntProfileMap
from models.saved_search import SavedSearch
from services import saved_searches as adapter
from tests.test_integration_inbox import owner


def item(pid, sid):
    return {
        "id": str(sid),
        "profile_id": str(pid),
        "name": "Python",
        "filters": {"q": "Python"},
        "min_score": 0,
        "notify_frequency": "daily",
        "notify_push": True,
        "is_active": True,
        "last_run_at": None,
        "total_matches": 2,
        "created_at": "2026-09-01T00:00:00Z",
    }


async def setup_owner(client, db, monkeypatch):
    token, _, pid = await owner(client, db)
    uid = await db.scalar(
        select(JobhuntProfileMap.user_id).where(
            JobhuntProfileMap.core_profile_id == pid
        )
    )
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", "synthetic-core-key")
    return {"Authorization": f"Bearer {token}"}, pid, uid


async def test_core_crud_and_manual_run_do_not_write_legacy(
    client, db_session, monkeypatch
):
    headers, pid, uid = await setup_owner(client, db_session, monkeypatch)
    sid = uuid.uuid4()
    row = item(pid, sid)
    requests = []

    def serve(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer synthetic-core-key"
        if request.url.path.endswith("/run"):
            return httpx.Response(
                202, json={"status": "dispatched", "search_id": str(sid)}
            )
        if request.method == "DELETE":
            assert request.headers["if-match"] == '"etag"'
            return httpx.Response(204)
        if request.method == "GET" and request.url.path == "/v1/saved-searches":
            return httpx.Response(200, json={"items": [row], "next_cursor": None})
        if request.method == "PUT":
            assert request.headers["if-match"] == '"etag"'
        return httpx.Response(
            201 if request.method == "POST" else 200,
            json=row,
            headers={"etag": '"etag"'},
        )

    monkeypatch.setattr(
        adapter,
        "default_client_factory",
        lambda: httpx.AsyncClient(
            base_url="http://core.invalid/v1",
            headers={"Authorization": "Bearer synthetic-core-key"},
            transport=httpx.MockTransport(serve),
        ),
    )
    listing = await client.get("/api/v1/searches", headers=headers)
    assert listing.status_code == 200 and listing.json()["total"] == 1
    assert listing.json()["data"][0]["user_id"] == str(uid)
    key = str(uuid.uuid4())
    created = await client.post(
        "/api/v1/searches",
        headers={**headers, "Idempotency-Key": key},
        json={"name": "Python"},
    )
    assert created.status_code == 201, created.text
    import json

    posted = next(r for r in requests if r.method == "POST")
    assert posted.headers["idempotency-key"] == key
    assert json.loads(posted.content)["execution_contract"] == "swissjob-v1"
    assert (
        await client.put(
            f"/api/v1/searches/{sid}", headers=headers, json={"name": "Python"}
        )
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/searches/{sid}/run", headers=headers)
    ).json() == {"status": "dispatched", "search_id": str(sid)}
    assert (
        await client.delete(f"/api/v1/searches/{sid}", headers=headers)
    ).status_code == 204
    assert await db_session.scalar(select(func.count()).select_from(SavedSearch)) == 0


async def test_una_busqueda_guardada_acepta_la_ubicacion_del_panel(
    client, db_session, monkeypatch
):
    """DT-148 A18-01: el panel de filtros ya no ofrece cantón sino `location`,
    y `SavedSearchesPage` hereda los filtros vigentes al crear la busqueda. El
    contrato de `filters` es `extra="forbid"`, asi que sin este campo el
    usuario que guardara una busqueda con la ubicacion puesta recibiria un
    **422** — un filtro que la interfaz ofrece y la API rechaza, que es
    exactamente el modo de fallo que A18-01 venia a cerrar.
    """
    requests: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json=item(pid, uuid.uuid4()))
        return httpx.Response(200, json={"items": [], "next_cursor": None, "total": 0})

    headers, pid, _uid = await setup_owner(client, db_session, monkeypatch)
    monkeypatch.setattr(
        adapter,
        "default_client_factory",
        lambda: httpx.AsyncClient(
            base_url="http://core.test/v1", transport=httpx.MockTransport(handler)
        ),
    )
    created = await client.post(
        "/api/v1/searches",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"name": "Zurich", "filters": {"location": "Zürich"}},
    )
    assert created.status_code == 201, created.text
    import json as _json

    enviado = _json.loads(next(r for r in requests if r.method == "POST").content)
    assert enviado["filters"]["location"] == "Zürich"


async def test_una_busqueda_guardada_rechaza_un_filtro_que_nadie_sirve(
    client, db_session, monkeypatch
):
    """El `extra="forbid"` se queda: una clave inventada falla en la ENTRADA
    (422 a quien la escribe) y no en el evaluador del nucleo, que abortaria el
    barrido de TODOS los usuarios (G3/P2-8)."""
    headers, _pid, _uid = await setup_owner(client, db_session, monkeypatch)
    rechazado = await client.post(
        "/api/v1/searches",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"name": "x", "filters": {"inventado": "si"}},
    )
    assert rechazado.status_code == 422, rechazado.text


@pytest.mark.parametrize("broken", ["unavailable", "shape", "owner", "repeat_cursor"])
async def test_core_failure_never_returns_local_data(
    client, db_session, monkeypatch, broken
):
    headers, pid, uid = await setup_owner(client, db_session, monkeypatch)
    db_session.add(SavedSearch(user_id=uid, name="local must not leak"))
    await db_session.commit()
    sid = uuid.uuid4()

    def serve(request):
        row = item(pid if broken != "owner" else uuid.uuid4(), sid)
        if broken == "unavailable":
            return httpx.Response(503)
        if broken == "shape":
            return httpx.Response(200, json={"items": [None]})
        return httpx.Response(
            200,
            json={
                "items": [row],
                "next_cursor": "loop" if broken == "repeat_cursor" else None,
            },
        )

    monkeypatch.setattr(
        adapter,
        "default_client_factory",
        lambda: httpx.AsyncClient(
            base_url="http://core.invalid/v1", transport=httpx.MockTransport(serve)
        ),
    )
    response = await client.get("/api/v1/searches", headers=headers)
    assert response.status_code == 503
    assert "local must not leak" not in response.text
