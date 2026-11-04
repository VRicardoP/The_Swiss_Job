"""H5/T8 — el límite por defecto cubre TODA la API, no sólo 5 rutas.

Antes: `Limiter(default_limits=["100/minute"])` estaba puesto pero **no se
aplicaba a nada**, porque slowapi sólo lo honra con su middleware y éste no
estaba instalado. Configuración muerta que se leía como protección.

El middleware propio aplica el bucket por cliente y camino sin depender de los
internos de SlowAPI. Las pruebas de comportamiento fijan que también cubre las
rutas incluidas sin decorador.
"""

import uuid

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from core.rate_limit import LimiteGlobalMiddleware, limiter


def _camino_nuevo() -> str:
    """Un camino que nadie ha usado todavía.

    El almacén de los buckets es el Redis REAL y su estado SOBREVIVE a la
    corrida: el bucket es (IP del cliente, camino), así que un `3/minute` sobre
    un camino fijo llega GASTADO en cualquier re-ejecución dentro del minuto.
    Estos tres tests pasaban en CI —donde Redis nace vacío— y fallaban en local
    al repetirlos; se detectó al lanzar el fichero dos veces seguidas, no en una.
    """
    return f"/api/v1/prueba/{uuid.uuid4().hex}"


def _app_con_ruta(camino: str, limite: str) -> FastAPI:
    """Réplica de la forma real: la ruta llega por `include_router`."""
    from fastapi import APIRouter

    router = APIRouter()

    @router.get(camino)
    async def handler():
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
    camino = _camino_nuevo()
    app = _app_con_ruta(camino, "3/minute")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://prueba") as c:
        codigos = [(await c.get(camino)).status_code for _ in range(5)]
    assert codigos[:3] == [200, 200, 200], codigos
    assert codigos[3:] == [429, 429], codigos


@pytest.mark.asyncio
async def test_el_429_dice_cual_es_el_limite_y_cuanto_esperar(limitador_activo):
    camino = _camino_nuevo()
    app = _app_con_ruta(camino, "1/minute")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://prueba") as c:
        await c.get(camino)
        r = await c.get(camino)
    assert r.status_code == 429
    assert "1 per 1 minute" in r.json()["detail"]
    assert int(r.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_con_el_limitador_apagado_no_estorba(limitador_activo):
    limiter.enabled = False
    camino = _camino_nuevo()
    app = _app_con_ruta(camino, "1/minute")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://prueba") as c:
        codigos = [(await c.get(camino)).status_code for _ in range(4)]
    assert codigos == [200] * 4


# --- A-17: un fallo del ALMACÉN no puede tirar la API -----------------------
#
# `limiter._limiter.hit` iba sin protección y el almacén es Redis. Un
# `ConnectionError` de `RedisStorage.incr` subía por `BaseHTTPMiddleware` y se
# convertía en 500 para CADA petición, `/health` incluida, porque este
# middleware está delante de todo: un corte de Redis pasaba de degradar el SSE
# y el scheduler a tumbar la API entera, y encima el healthcheck del compose
# reiniciaba el contenedor en bucle.
#
# Hay DOS caminos y degradan distinto, a propósito:
#
#  - `LimiteGlobalMiddleware` falla ABIERTO (sirve sin límite, con un ERROR en
#    el log). Su bucket es el global de 240/min: una defensa contra el abuso, no
#    una invariante de corrección. Perderlo un rato es tolerable; perder la API
#    no.
#  - Las rutas DECORADAS (las cuatro de /auth) degradan a contar EN MEMORIA
#    vía `in_memory_fallback_enabled`, así que conservan su límite —por proceso,
#    o sea 2× con `-w 2`—. Son las sensibles, y por eso no se rinden.
#
# La asimetría es deliberada: `swallow_errors` por sí solo NO servía para las
# decoradas, y hubo que MEDIRLO para verlo (el decorador lee
# `request.state.view_rate_limit` sin guarda en `extension.py:742`, y ese
# atributo sólo se fija si la evaluación llegó al final → AttributeError → 500
# igual, con el error ya tragado).


class _AlmacenRoto:
    """Doble del almacén de `limits` que falla como lo haría Redis caído."""

    def __init__(self, excepcion):
        self._excepcion = excepcion
        self.intentos = 0

    def hit(self, *_args, **_kwargs):
        self.intentos += 1
        raise self._excepcion


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "excepcion",
    [
        ConnectionError("Error 111 connecting to redis:6379. Connection refused."),
        TimeoutError("Timeout reading from redis"),
        RuntimeError("fallo inesperado del almacén"),
    ],
    ids=["conexion", "timeout", "inesperado"],
)
async def test_un_fallo_del_almacen_sirve_la_peticion_en_vez_de_dar_500(
    limitador_activo, monkeypatch, excepcion
):
    roto = _AlmacenRoto(excepcion)
    monkeypatch.setattr(limiter, "_limiter", roto)
    camino = _camino_nuevo()
    app = _app_con_ruta(camino, "1/minute")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as cliente:
        respuesta = await cliente.get(camino)

    assert respuesta.status_code == 200, "un almacén caído no puede tumbar la ruta"
    assert respuesta.json() == {"ok": True}
    assert roto.intentos == 1, "se intentó contar antes de rendirse"


@pytest.mark.asyncio
async def test_el_fallo_del_almacen_se_registra_como_ERROR(
    limitador_activo, monkeypatch, caplog
):
    """Fallar abierto en silencio sería peor que fallar: no se distinguiría
    «Redis cayó un segundo» de «llevamos una semana sin límite»."""
    monkeypatch.setattr(limiter, "_limiter", _AlmacenRoto(ConnectionError("caído")))
    camino = _camino_nuevo()
    app = _app_con_ruta(camino, "1/minute")

    with caplog.at_level("ERROR", logger="core.rate_limit"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as cliente:
            await cliente.get(camino)

    # `getMessage()` y no `.message`: el segundo no existe hasta que un handler
    # formatea el registro, y aquí no hay ninguno.
    assert any("sin almacén" in r.getMessage() for r in caplog.records), (
        "el fallo abierto debe verse en el log"
    )


@pytest.mark.asyncio
async def test_con_el_almacen_SANO_el_limite_sigue_aplicandose(limitador_activo):
    """Control de que fallar abierto no desactivó el límite de siempre.

    OJO AL AISLAMIENTO: el almacén es el Redis REAL y su estado SOBREVIVE entre
    tests, así que un bucket `1/minute` sobre el camino que ya usan las pruebas
    de arriba llega gastado y la primera petición sale 429. Por eso cada caso
    que necesite un bucket FRESCO tiene que pedir un camino propio — es la clave
    del bucket, junto con la IP del cliente.
    """
    camino = _camino_nuevo()
    app = _app_con_ruta(camino, "1/minute")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as cliente:
        primera = await cliente.get(camino)
        segunda = await cliente.get(camino)

    assert primera.status_code == 200, "bucket fresco: la primera debe pasar"
    assert segunda.status_code == 429, "el límite debe seguir vivo con Redis sano"


@pytest.mark.asyncio
async def test_una_ruta_DECORADA_tambien_sobrevive_al_almacen_caido(
    limitador_activo, monkeypatch
):
    """Las rutas con `@limiter.limit(...)` —las cuatro de /auth— no pasan por
    `LimiteGlobalMiddleware`, van por la maquinaria de slowapi. Sin
    `swallow_errors` un corte de Redis las devolvía en 500, o sea que tiraba
    justamente el camino de login.

    Se comprueba el COMPORTAMIENTO y no `limiter._swallow_errors`: este fichero
    ya documenta que acoplarse a los internos de slowapi es lo que ha resultado
    frágil, y un atributo privado puede cambiar de nombre sin cambiar el efecto.
    """
    from fastapi import APIRouter

    camino = _camino_nuevo()
    router = APIRouter()

    @router.get(camino)
    @limiter.limit("5/minute")
    async def decorada(request: Request):
        return {"ok": True}

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(router)
    # Sin `LimiteGlobalMiddleware` a propósito: se prueba el OTRO camino.

    monkeypatch.setattr(limiter, "_limiter", _AlmacenRoto(ConnectionError("caído")))
    # `_storage_dead` es una bandera GLOBAL del limiter y el fallback la deja en
    # True: sin monkeypatch (que la restaura al salir) este test envenenaba a
    # los siguientes del fichero, que daban por muerto un almacén sano. Lo
    # descubrió el control negativo, no la corrida en verde.
    monkeypatch.setattr(limiter, "_storage_dead", False)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as cliente:
        respuesta = await cliente.get(camino)

    assert respuesta.status_code == 200, (
        "una ruta decorada no puede devolver 500 porque Redis esté caído"
    )
    # Y no sólo no revienta: con `in_memory_fallback_enabled` el límite SIGUE
    # contando, en memoria. `swallow_errors` por sí solo no habría llegado aquí
    # —el decorador lee `request.state.view_rate_limit` sin guarda— y hubo que
    # MEDIRLO para descubrirlo: el atributo estaba bien puesto y el
    # comportamiento no estaba.
    assert limiter._storage_dead is True, "debió marcar el almacén como muerto"
