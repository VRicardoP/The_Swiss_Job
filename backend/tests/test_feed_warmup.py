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

    with (
        patch.object(warm, "_usuarios_enrolados", AsyncMock(return_value=usuarios)),
        patch("services.matching.resolve_matching", AsyncMock(return_value=backend)),
    ):
        resumen = await warm.calentar_una_vez()

    assert resumen == {"usuarios": 2, "calentados": 2, "fallos": 0}
    assert backend.warm_feed.await_count == 2
    # NO se construyen vistas: sólo se calienta el recorrido
    backend.results.assert_not_called()


@pytest.mark.asyncio
async def test_un_usuario_que_falla_no_arrastra_a_los_demas():
    usuarios = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    backend = MagicMock()
    backend.warm_feed = AsyncMock(side_effect=[RuntimeError("core caído"), 1800, 1800])

    with (
        patch.object(warm, "_usuarios_enrolados", AsyncMock(return_value=usuarios)),
        patch("services.matching.resolve_matching", AsyncMock(return_value=backend)),
    ):
        resumen = await warm.calentar_una_vez()

    assert resumen == {"usuarios": 3, "calentados": 2, "fallos": 1}


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

    assert resumen == {"usuarios": 1, "calentados": 0, "fallos": 0}


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
