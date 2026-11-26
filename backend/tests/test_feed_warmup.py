"""Punto 5 §10.3-ter — el recorrido del feed se calienta fuera de la petición.

Lo que estas pruebas fijan, y que no es obvio leyendo el código:

- Calentar recorre el feed y deja la caché lista, pero **no construye vistas**:
  las vistas dependen del overlay local y cachearlas serviría estado rancio.
- Un fallo al calentar **no degrada nada**: la petición siguiente recorre como
  siempre. Un bucle de fondo que tumbe el arranque sería peor que no tenerlo.
- Si el backend de matching es el LOCAL (sin core), no hay recorrido que
  calentar y la pasada no se inventa trabajo.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.matching import warm


@pytest.mark.asyncio
async def test_calienta_cada_usuario_enrolado():
    usuarios = [uuid.uuid4(), uuid.uuid4()]
    backend = MagicMock()
    backend.warm_feed = AsyncMock(return_value=1800)
    backend.warm_facets = AsyncMock(return_value=False)

    with (
        patch.object(warm, "_usuarios_enrolados", AsyncMock(return_value=usuarios)),
        patch("services.matching.resolve_matching", AsyncMock(return_value=backend)),
    ):
        resumen = await warm.calentar_una_vez()

    assert resumen == {
        "usuarios": 2,
        "calentados": 2,
        "facetas": 0,
        "fallos": 0,
        "traducidos": 0,
    }
    assert backend.warm_feed.await_count == 2
    # NO se construyen vistas: sólo se calienta el recorrido
    backend.results.assert_not_called()


@pytest.mark.asyncio
async def test_un_usuario_que_falla_no_arrastra_a_los_demas():
    usuarios = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    backend = MagicMock()
    backend.warm_feed = AsyncMock(side_effect=[RuntimeError("core caído"), 1800, 1800])
    backend.warm_facets = AsyncMock(return_value=False)

    with (
        patch.object(warm, "_usuarios_enrolados", AsyncMock(return_value=usuarios)),
        patch("services.matching.resolve_matching", AsyncMock(return_value=backend)),
    ):
        resumen = await warm.calentar_una_vez()

    assert resumen == {
        "usuarios": 3,
        "calentados": 2,
        "facetas": 0,
        "fallos": 1,
        "traducidos": 0,
    }


@pytest.mark.asyncio
async def test_backend_local_no_tiene_recorrido_que_calentar():
    """El backend local no expone `warm_feed`: la pasada no inventa trabajo."""
    backend = MagicMock(spec=[])  # sin warm_feed

    with (
        patch.object(
            warm, "_usuarios_enrolados", AsyncMock(return_value=[uuid.uuid4()])
        ),
        patch("services.matching.resolve_matching", AsyncMock(return_value=backend)),
    ):
        resumen = await warm.calentar_una_vez()

    assert resumen == {
        "usuarios": 1,
        "calentados": 0,
        "facetas": 0,
        "fallos": 0,
        "traducidos": 0,
    }


class _RedisMarcas:
    def __init__(self, activos=(), caido=False):
        self.datos = {f"feed:active:{u}".encode(): b"1" for u in activos}
        self.caido = caido
        self.puestas: list[tuple] = []

    async def mget(self, claves):
        if self.caido:
            raise ConnectionError("redis caído")
        return [self.datos.get(k.encode()) for k in claves]

    async def set(self, clave, valor, ex=None):
        self.puestas.append((clave, ex))


async def _pasada(usuarios, redis):
    backend = MagicMock()
    backend.warm_feed = AsyncMock(return_value=1800)
    backend.warm_facets = AsyncMock(return_value=False)
    backend.cached_feed_titles = None
    with (
        patch.object(warm, "_usuarios_enrolados", AsyncMock(return_value=usuarios)),
        patch("services.matching.resolve_matching", AsyncMock(return_value=backend)),
        patch.object(warm, "_traducir_en_fondo", AsyncMock(return_value=0)),
    ):
        await warm.calentar_una_vez(redis)
    return [c.args[0] for c in backend.warm_feed.await_args_list]


@pytest.mark.asyncio
async def test_solo_se_calienta_a_quien_tiene_marca_de_actividad():
    activo, inactivo = uuid.uuid4(), uuid.uuid4()
    calentados = await _pasada([activo, inactivo], _RedisMarcas(activos=[activo]))
    assert calentados == [activo]


@pytest.mark.asyncio
async def test_sin_redis_o_con_redis_caido_se_calienta_a_todos():
    usuarios = [uuid.uuid4(), uuid.uuid4()]
    assert await _pasada(usuarios, None) == usuarios
    assert await _pasada(usuarios, _RedisMarcas(caido=True)) == usuarios


@pytest.mark.asyncio
async def test_la_marca_caduca_en_un_dia():
    redis = _RedisMarcas()
    user_id = uuid.uuid4()
    await warm.mark_active(redis, user_id)
    assert redis.puestas == [(f"feed:active:{user_id}", 86_400)]
    await warm.mark_active(None, user_id)  # sin Redis: no hace nada, no lanza


@pytest.mark.asyncio
async def test_servir_el_feed_y_las_facetas_marca_actividad(monkeypatch):
    from config import settings
    from routers import match as router

    monkeypatch.setattr(settings, "CORE_MATCH_FACETS_ENABLED", True)
    redis = _RedisMarcas()
    request = MagicMock()
    request.app.state.redis_client = redis
    user = MagicMock()
    user.id = uuid.uuid4()
    core = MagicMock()
    core.facets = AsyncMock(return_value={})
    core.results = AsyncMock(return_value=([], 0))
    monkeypatch.setattr(router, "CoreMatching", lambda _db: core)
    monkeypatch.setattr(router, "overlay_school_results", AsyncMock(return_value=[]))
    monkeypatch.setattr(router, "_build_results_response", AsyncMock())
    db = MagicMock()
    db.refresh = AsyncMock()

    await router.get_match_facets(request, user, db)
    await router.get_match_results(
        request, user, db, limit=20, offset=0, translate=False, category="A"
    )

    clave = f"feed:active:{user.id}"
    assert redis.puestas == [(clave, 86_400), (clave, 86_400)]


@pytest.mark.asyncio
async def test_el_interruptor_apaga_el_bucle():
    from config import settings

    original = settings.FEED_WARMUP_ENABLED
    settings.FEED_WARMUP_ENABLED = False
    try:
        with patch.object(warm, "calentar_una_vez", AsyncMock()) as pasada:
            await warm.run_feed_warmup()  # retorna en vez de quedarse en bucle
            pasada.assert_not_awaited()
    finally:
        settings.FEED_WARMUP_ENABLED = original
