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


# --- A-19: el overlay ESCOLAR también viaja por el bus --------------------
#
# `schools/presentation.py` cachea 60 s EN PROCESO el `application_status` y el
# `draft_content`, y `watchlist_state._write` los invalidaba SÓLO en su propio
# proceso: no publicaba en el bus, al contrario que `CoreFeedback._write`. Con
# gunicorn `-w 2`, marcar «Applied» o guardar un borrador en el worker A podía
# servirse como «detected» o sin borrador desde el B hasta 60 s. Es la misma
# regresión M3/T12 que motivó este bus, repartida entre procesos, en la otra
# caché — y rompía la cota «el overlay LOCAL se relee siempre».


@pytest.mark.asyncio
async def test_mecanismo_el_oyente_invalida_tambien_la_presentacion_escolar():
    """Lo que un proceso publica, el oyente del otro lo aplica sobre LAS DOS
    cachés. Contra el Redis real: simularlo aquí sería probar el simulador.

    Control negativo: quitando `clear_school_presentation_cache` del oyente,
    este caso se queda esperando y falla por plazo.
    """
    from services.schools import presentation

    pid = str(uuid.uuid4())
    presentation._overlay_cache[pid] = (0.0, [], {})

    oyente = asyncio.create_task(cache_bus.escuchar())
    try:
        await asyncio.sleep(0.3)  # que el subscribe esté hecho
        await cache_bus.publicar(pid)
        assert await _esperar(lambda: pid not in presentation._overlay_cache), (
            "el otro proceso debía haber invalidado su overlay escolar"
        )
    finally:
        oyente.cancel()
        await asyncio.gather(oyente, return_exceptions=True)


@pytest.mark.asyncio
async def test_cableado_watchlist_state_PUBLICA_al_invalidar(monkeypatch):
    """La línea que se puede borrar sin que nada más se entere, y por eso tiene
    prueba propia — el mismo razonamiento que la cabecera de este fichero.

    Se ejercita `_write` DE VERDAD, con sus colaboradores simulados. La primera
    versión de este test llamaba a `publicar` a mano y luego afirmaba que se
    había llamado: tautológico, y habría seguido verde con la línea borrada.

    Control negativo: quitando el `await publicar(pid)` de
    `watchlist_state._write`, este caso falla.
    """
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from services.schools import watchlist_state
    from services.schools.watchlist_state import CoreWatchlist

    publicados: list = []

    async def _publicar(pid=None):
        publicados.append(pid)

    monkeypatch.setattr("services.matching.cache_bus.publicar", _publicar, raising=True)

    pid = uuid.uuid4()
    job = SimpleNamespace(
        tags=["monitor-1"],
        title="Profesor de primaria",
        company="ISB",
        url="https://ejemplo.test/1",
        school_job_id=None,
    )
    match = SimpleNamespace(created_at=datetime.now(timezone.utc))

    escrituras: list = []

    class _Cliente:
        async def monitors(self):
            return [SimpleNamespace(external_ref="monitor-1", id="m1")]

        async def write_state(self, *a, **k):
            escrituras.append((a, k))
            return True

    cliente = CoreWatchlist.__new__(CoreWatchlist)
    cliente._client = _Cliente()

    async def _get_match(_user_id, _job_hash):
        return (match, job)

    async def _profile(_user_id):
        return pid

    monkeypatch.setattr(cliente, "get_match", _get_match, raising=False)
    monkeypatch.setattr(cliente, "_profile", _profile, raising=False)

    assert await cliente._write(uuid.uuid4(), "hash-1", {"status": "applied"}) is True
    assert escrituras, "la escritura al core debía ocurrir"
    assert publicados == [pid], (
        "invalidar en local sin publicar deja al otro worker con el estado viejo"
    )
    assert watchlist_state is not None  # el módulo es el que se está probando
