"""I-F3 — la caché del recorrido se invalida ENTRE procesos.

`core_client._feed_cache` vive en proceso y gunicorn corre con `-w 2`: limpiar la
caché en el worker que atiende una escritura no limpia la del otro. `cache_bus`
reparte la invalidación por Redis pub/sub. Hasta el 2026-09-28 este invariante
apuntaba a `test_feed_budget_y_degradacion.py`, que prueba OTRA cosa: quitar la
publicación dejaba la suite verde (auditoría instrumentada del 27-09, CHECK_ROTO).

Dos familias de casos, a propósito separadas:

- **Mecanismo**: lo que un proceso publica, el oyente del otro lo aplica sobre su
  propia caché. Va contra el Redis real del entorno de pruebas: simular Redis aquí
  sería probar el simulador.
- **Cableado**: quien invalida su caché local TAMBIÉN publica. Es la línea que se
  puede borrar sin que nada más se entere, y por eso tiene prueba propia.
"""

import asyncio
import uuid

import pytest

from services.matching import cache_bus, core_client


async def _esperar(condicion, *, plazo=5.0):
    fin = asyncio.get_running_loop().time() + plazo
    while asyncio.get_running_loop().time() < fin:
        if condicion():
            return True
        await asyncio.sleep(0.05)
    return False


@pytest.fixture
async def oyente():
    """El «otro proceso»: un `escuchar()` sobre la misma caché de este intérprete."""
    tarea = asyncio.create_task(cache_bus.escuchar())
    # La suscripción no es instantánea; se publica basura hasta que el oyente
    # demuestre estar vivo limpiando una clave centinela.
    core_client._feed_cache["__centinela__"] = ("v", [], 0)
    for _ in range(60):
        await cache_bus.publicar("__centinela__")
        if "__centinela__" not in core_client._feed_cache:
            break
        await asyncio.sleep(0.05)
    assert "__centinela__" not in core_client._feed_cache, (
        "el oyente nunca se suscribió"
    )
    yield
    tarea.cancel()
    try:
        await tarea
    except asyncio.CancelledError:
        pass


class TestMecanismo:
    async def test_publicar_un_perfil_lo_borra_de_la_cache_del_oyente(self, oyente):
        core_client._feed_cache["p1"] = ("v1", [], 0)
        core_client._feed_cache["p2"] = ("v2", [], 0)

        await cache_bus.publicar("p1")

        assert await _esperar(lambda: "p1" not in core_client._feed_cache)
        assert "p2" in core_client._feed_cache, "borró más de lo publicado"
        core_client._feed_cache.clear()

    async def test_publicar_sin_perfil_vacia_la_cache_entera(self, oyente):
        core_client._feed_cache["p1"] = ("v1", [], 0)
        core_client._feed_cache["p2"] = ("v2", [], 0)

        await cache_bus.publicar()

        assert await _esperar(lambda: not core_client._feed_cache)

    async def test_sin_oyente_la_cache_no_cambia(self):
        """Control del mecanismo: la limpieza la hace el oyente, no `publicar`.
        Sin él, publicar no toca nada — que es exactamente el fallo M3 original."""
        core_client._feed_cache["p1"] = ("v1", [], 0)
        await cache_bus.publicar("p1")
        await asyncio.sleep(0.3)
        assert "p1" in core_client._feed_cache
        core_client._feed_cache.clear()


class TestCableado:
    async def test_la_escritura_de_feedback_publica_el_perfil(
        self, db_session, monkeypatch
    ):
        from services.matching.feedback import CoreFeedback

        publicados = []

        async def registrar(profile_id=None):
            publicados.append(profile_id)

        monkeypatch.setattr(cache_bus, "publicar", registrar)

        pid = uuid.uuid4()
        cliente = CoreFeedback(db_session)

        async def perfil(_user_id):
            return pid

        async def objetivo(_job_hash):
            return "vacancies", uuid.uuid4()

        class _Resp404:
            status_code = 404

        async def peticion(*_a, **_k):
            return _Resp404()

        monkeypatch.setattr(cliente, "_profile", perfil)
        monkeypatch.setattr(cliente, "_target", objetivo)
        monkeypatch.setattr(cliente, "_request", peticion)

        # Con 404 el core no confirma nada, y AUN ASÍ se publica: un 404 no
        # prueba que nada cambiara (comentario en `_write`).
        resultado = await cliente._write(
            uuid.uuid4(), "hash", "feedback", {"feedback": "thumbs_up"}
        )

        assert resultado is None
        assert publicados == [pid], "la escritura no avisó al otro worker"

    async def test_el_borrado_de_perfil_publica_sin_esperar(self, monkeypatch):
        from services.profile_erasure import clear_erased_caches

        publicados = []
        monkeypatch.setattr(cache_bus, "publicar_sin_esperar", publicados.append)

        clear_erased_caches("perfil-borrado")

        assert publicados == ["perfil-borrado"]

    async def test_el_borrado_sin_perfil_no_publica(self, monkeypatch):
        """Control: `None` significa «nada que invalidar», no «invalídalo todo»."""
        from services.profile_erasure import clear_erased_caches

        publicados = []
        monkeypatch.setattr(cache_bus, "publicar_sin_esperar", publicados.append)

        clear_erased_caches(None)

        assert publicados == []
