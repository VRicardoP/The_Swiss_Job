"""H13/T10 — el JWT sale de la query string del SSE.

`EventSource` no sabe mandar cabeceras, así que el token de acceso viajaba en
la URL: de ahí pasaba a los logs de acceso de nginx, al historial del navegador
y a la cabecera `Referer`, y seguía sirviendo 30 minutos. Ahora viaja un vale
que se canjea **una sola vez** y caduca en 30 segundos.

Cada prueba fija una condición distinta: que el vale haga falta, que valga una
vez y no dos, que caduque, y que no sirva el de otro.
"""

import uuid

import pytest

from routers.notifications import TICKET_TTL_SEGUNDOS, _clave_ticket
from tests.conftest import random_email


@pytest.fixture(autouse=True)
def _redis_en_el_app(redis_client):
    """El cliente de pruebas no corre el lifespan, que es quien pone el Redis
    en `app.state`. Sin esto el endpoint responde 503 y la prueba mediría el
    andamiaje, no el vale."""
    from main import app

    previo = getattr(app.state, "redis_client", None)
    app.state.redis_client = redis_client
    yield
    app.state.redis_client = previo


@pytest.fixture
async def auth_headers(client):
    """Una cuenta recién creada y su Bearer, que es lo que pide el vale."""
    correo = random_email()
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": correo, "password": "TestPass123!", "gdpr_consent": True},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_pedir_vale_exige_estar_autenticado(client):
    r = await client.post("/api/v1/notifications/stream-ticket")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_el_vale_llega_con_su_caducidad(client, auth_headers):
    r = await client.post("/api/v1/notifications/stream-ticket", headers=auth_headers)
    assert r.status_code == 201
    cuerpo = r.json()
    assert uuid.UUID(cuerpo["ticket"])  # hex válido
    assert cuerpo["expires_in"] == TICKET_TTL_SEGUNDOS


@pytest.mark.asyncio
async def test_el_stream_ya_no_acepta_el_token_en_la_url(client, auth_headers):
    """La regresión que se quiere impedir: volver a admitir `?token=`."""
    jwt = auth_headers["Authorization"].split(" ", 1)[1]
    r = await client.get(f"/api/v1/notifications/stream?token={jwt}")
    # Falta el parámetro obligatorio `ticket` => 422, nunca un stream abierto.
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_un_vale_inventado_no_abre_nada(client):
    r = await client.get("/api/v1/notifications/stream?ticket=" + uuid.uuid4().hex)
    assert r.status_code == 401
    assert "ticket" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_el_vale_vale_UNA_vez(client, auth_headers, redis_client):
    ticket = (
        await client.post("/api/v1/notifications/stream-ticket", headers=auth_headers)
    ).json()["ticket"]
    # El canje lo borra atómicamente (GETDEL); comprobamos el efecto en Redis
    # en vez de abrir un stream infinito.
    assert await redis_client.get(_clave_ticket(ticket)) is not None
    from routers.notifications import _usuario_del_ticket

    class _Peticion:
        def __init__(self, app):
            self.app = app

    peticion = _Peticion(client._transport.app)
    primero = await _usuario_del_ticket(peticion, ticket)
    assert isinstance(primero, uuid.UUID)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as fallo:
        await _usuario_del_ticket(peticion, ticket)
    assert fallo.value.status_code == 401
    assert await redis_client.get(_clave_ticket(ticket)) is None


@pytest.mark.asyncio
async def test_el_vale_caduca(client, auth_headers, redis_client):
    ticket = (
        await client.post("/api/v1/notifications/stream-ticket", headers=auth_headers)
    ).json()["ticket"]
    ttl = await redis_client.ttl(_clave_ticket(ticket))
    assert 0 < ttl <= TICKET_TTL_SEGUNDOS
