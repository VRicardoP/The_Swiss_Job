"""H5/T8 — el límite por defecto cubre TODA la API, no sólo 5 rutas.

Antes: `Limiter(default_limits=["100/minute"])` estaba puesto pero **no se
aplicaba a nada**, porque slowapi sólo lo honra con su middleware y éste no
estaba instalado. Configuración muerta que se leía como protección.

Y el middleware de slowapi tampoco servía: busca el handler con
`hasattr(route, "endpoint")`, y desde FastAPI 0.141 los routers incluidos viven
en `app.routes` como `_IncludedRouter`, sin ese atributo. Devuelve `None`,
`_should_exempt(None)` es True y **exime todas las rutas de la API**. Medido en
este árbol antes de escribir el middleware propio: 260 peticiones seguidas a
`/api/v1/jobs/stats` salieron las 260 con 200 y no dejaron ni una clave en Redis.
La última prueba de abajo es la que impide volver a ese estado.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from core.rate_limit import LimiteGlobalMiddleware, limiter


def _app_con_router(limite: str) -> FastAPI:
    """Réplica de la forma real: la ruta llega por `include_router`."""
    from fastapi import APIRouter

    router = APIRouter(prefix="/api/v1/prueba")

    @router.get("/sin-decorador")
    async def sin_decorador():
        return {"ok": True}

    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(router)
    app.add_middleware(LimiteGlobalMiddleware, limite=limite)
    return app


@pytest.fixture
def limitador_activo():
    original = limiter.enabled
    limiter.enabled = True
    yield
    limiter.enabled = original


@pytest.mark.asyncio
async def test_una_ruta_sin_decorador_acaba_devolviendo_429(limitador_activo):
    app = _app_con_router("3/minute")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://prueba") as c:
        codigos = [
            (await c.get("/api/v1/prueba/sin-decorador")).status_code for _ in range(5)
        ]
    assert codigos[:3] == [200, 200, 200], codigos
    assert codigos[3:] == [429, 429], codigos


@pytest.mark.asyncio
async def test_el_429_dice_cual_es_el_limite_y_cuanto_esperar(limitador_activo):
    app = _app_con_router("1/minute")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://prueba") as c:
        await c.get("/api/v1/prueba/sin-decorador")
        r = await c.get("/api/v1/prueba/sin-decorador")
    assert r.status_code == 429
    assert "1 per 1 minute" in r.json()["detail"]
    assert int(r.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_con_el_limitador_apagado_no_estorba(limitador_activo):
    limiter.enabled = False
    app = _app_con_router("1/minute")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://prueba") as c:
        codigos = [
            (await c.get("/api/v1/prueba/sin-decorador")).status_code for _ in range(4)
        ]
    assert codigos == [200] * 4


@pytest.mark.asyncio
async def test_el_middleware_de_slowapi_NO_habria_servido():
    """Control de la decisión: documenta por qué hay middleware propio.

    Si un día FastAPI vuelve a aplanar los routers y esto empieza a encontrar
    el handler, la prueba falla y avisa de que se puede simplificar.
    """
    from slowapi.middleware import _find_route_handler, _should_exempt

    app = _app_con_router("10/minute")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/prueba/sin-decorador",
        "headers": [],
        "root_path": "",
    }
    handler = _find_route_handler(app.routes, scope)
    assert handler is None, "FastAPI ya aplana los routers: revisar si sobra el propio"
    assert _should_exempt(limiter, handler) is True
