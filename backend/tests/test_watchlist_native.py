"""A native observation is actionable without any legacy Job or MatchResult."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from config import settings
from services.schools.http_client import SchoolClient
from services.schools.watchlist_state import CoreWatchlist
from services.schools.port import CoreUnavailableError
from tests.seeds import seeded  # noqa: F401  (fixture de pytest: se importa para que la resuelva por nombre)


@pytest.mark.asyncio
async def test_native_school_can_create_draft_without_local_corpus(
    seeded,  # noqa: F811  (la fixture, no una redefinición)
    db_session,
    monkeypatch,
):
    user_id, _, _ = seeded
    monkeypatch.setattr(settings, "CORE_FEEDBACK_ENABLED", True)
    mid, jid = uuid.uuid4(), uuid.uuid4()
    ref = "f" * 32
    client = SchoolClient()
    client.states = AsyncMock(return_value=[])
    client.monitors = AsyncMock(
        return_value=[
            SimpleNamespace(
                id=mid,
                external_ref="school-example",
                settings={"name": "Example School"},
            )
        ]
    )
    client.request = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": str(jid),
                        "monitor_id": str(mid),
                        "source_ref": ref,
                        "created_at": "2026-09-19T10:00:00Z",
                        "metadata": {
                            "title": "IT Technician",
                            "url": "https://school.example/job/1",
                        },
                    }
                ],
                "next_cursor": None,
            },
        )
    )
    client.write_state = AsyncMock()
    service = CoreWatchlist(db_session, client)
    pair = await service.get_match(user_id, ref)
    assert pair[0].application_status == "detected"
    assert pair[1].title == "IT Technician"
    assert await service.save_draft(user_id, ref, "Draft, not an email") is True
    client.write_state.assert_awaited_once()
    call = client.write_state.await_args
    assert call.kwargs["school_job_id"] == jid
    assert call.args[3] == {"draft_content": "Draft, not an email"}
    assert call.kwargs["context"]["job_url"] == "https://school.example/job/1"
    client.request.assert_awaited_with(
        "GET", "/school-jobs", params={"dedup_key": ref, "limit": 2}
    )


@pytest.mark.asyncio
async def test_native_school_lookup_refuses_ambiguous_identity(
    seeded,  # noqa: F811  (la fixture, no una redefinición)
    db_session,
    monkeypatch,
):
    user_id, _, _ = seeded
    monkeypatch.setattr(settings, "CORE_FEEDBACK_ENABLED", True)
    client = SchoolClient()
    client.states = AsyncMock(return_value=[])
    client.request = AsyncMock(
        return_value=httpx.Response(200, json={"items": [{}, {}], "next_cursor": None})
    )
    client.write_state = AsyncMock()
    with pytest.raises(CoreUnavailableError):
        await CoreWatchlist(db_session, client).save_draft(user_id, "f" * 32, "private")
    client.write_state.assert_not_called()


# --- A-23: la lista del BFF se había quedado una fuente corta --------------


def test_las_fuentes_vigiladas_son_LAS_del_nucleo():
    """`_WATCHLIST_SOURCES` duplica a mano la lista de fuentes escolares del
    núcleo, y se quedó en 8 de las 9: faltaba `swiss_schools_vis`. Punto ciego
    permanente — si esa fuente se apagaba por el kill-switch de bloqueos o
    dejaba de completar cosechas, el monitor de la watchlist no lo decía.

    No se DERIVA del núcleo en tiempo de ejecución a propósito: eso metería una
    llamada de red en una tarea Celery, con su propio modo de fallo, y rompería
    la frontera BFF↔núcleo, que hoy está limpia (`grep -rn "from jobhunt_core"
    backend` → 0). Se comprueba leyendo el fichero como TEXTO, que detecta la
    deriva sin importar nada.

    Se salta si el árbol del núcleo no está montado —el contenedor del BFF sólo
    monta `./backend`—; en CI y con el venv del host sí está, que son los dos
    sitios donde la suite es autoritativa.
    """
    import re
    from pathlib import Path

    from tasks.watchlist_tasks import _WATCHLIST_SOURCES

    # Se barre el DIRECTORIO entero y no un fichero: la familia está repartida
    # en dos módulos —`native_swiss_schools.py` y `native_isp_workday.py`, que
    # es donde vive `swiss_schools_isp`—, y la primera versión de este test
    # miraba sólo el primero y daba `isp` por sobrante. Barrer el directorio
    # cubre además un décimo colegio que llegue en un módulo nuevo.
    providers = (
        Path(__file__).resolve().parents[2] / "jobhunt_core" / "harvest" / "providers"
    )
    if not providers.is_dir():
        pytest.skip("el árbol del núcleo no está montado en este entorno")

    del_nucleo = set()
    for fichero in providers.glob("*.py"):
        del_nucleo |= set(
            re.findall(r"swiss_schools_[a-z]+", fichero.read_text(encoding="utf-8"))
        )
    assert del_nucleo, "no se encontró ninguna fuente en los providers del núcleo"

    faltan = del_nucleo - set(_WATCHLIST_SOURCES)
    sobran = set(_WATCHLIST_SOURCES) - del_nucleo
    assert not faltan, (
        f"el BFF no vigila fuentes que el núcleo cosecha: {sorted(faltan)}"
    )
    assert not sobran, (
        f"el BFF vigila fuentes que el núcleo no cosecha: {sorted(sobran)}"
    )


# --- A-22: una fuente ilegible no puede tumbar las demás, ni salir en verde ---


@pytest.mark.asyncio
async def test_una_fuente_ilegible_no_aborta_la_comprobacion_de_las_demas():
    """`asyncio.gather` sin `return_exceptions` abortaba TODAS en cuanto UNA
    fallaba —un 404 de fuente no registrada, un 5xx, un timeout—, así que el
    monitor podía llevar meses sin comprobar nada por una sola fuente rota.

    Control negativo: quitando `return_exceptions=True`, este caso propaga la
    excepción en vez de devolver la terna.
    """
    from tasks import watchlist_tasks

    rota = watchlist_tasks._WATCHLIST_SOURCES[0]

    def handler(request):
        fuente = request.url.path.split("/")[-2]
        if fuente == rota:
            raise httpx.ConnectError("fuente inalcanzable")
        return httpx.Response(
            200,
            json={
                "source": fuente,
                "last_complete_at": None,
                "enabled_scopes": 1,
                "observed_scopes": 1,
                "max_consecutive_failures": 0,
                "update_in_progress": False,
            },
        )

    def factory():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://core.test/v1"
        )

    salud, ilegibles = await watchlist_tasks._core_watchlist_health(factory)

    esperadas = len(watchlist_tasks._WATCHLIST_SOURCES)
    assert len(salud) == esperadas - 1, "las demás sí se comprobaron"
    assert [s for s, _ in ilegibles] == [rota]


@pytest.mark.asyncio
async def test_una_fuente_ilegible_sale_como_INCIDENCIA_no_como_silencio(
    db_session, monkeypatch
):
    """«No lo sé» no puede leerse como «está bien»: es información distinta de
    una fuente que está mal, y antes se la comía el gather."""
    from contextlib import asynccontextmanager
    from unittest.mock import patch

    from tasks import watchlist_tasks

    rota = watchlist_tasks._WATCHLIST_SOURCES[0]

    class _Redis:
        async def get(self, *a, **k):
            return None

        async def set(self, *a, **k):
            return True

        async def close(self):
            return None

    monkeypatch.setattr("redis.from_url", lambda *a, **k: _Redis())

    async def _salud(_factory=None):
        return [], [(rota, RuntimeError("Core inaccesible"))]

    @asynccontextmanager
    async def _session():
        yield db_session

    with (
        patch.object(watchlist_tasks, "_core_watchlist_health", _salud),
        patch("database.task_session", new=_session),
    ):
        resultado = await watchlist_tasks._check_health_async(None)

    assert resultado["status"] == "issues", resultado
    assert resultado["issues"] >= 1, resultado


def test_un_fallo_de_la_tarea_de_salud_NO_sale_como_SUCCESS(monkeypatch):
    """Antes devolvía `{"status": "error"}` y Celery marcaba SUCCEEDED. Nadie
    consume ese dict, así que el único rastro de que el monitor no pudo
    monitorizar era una línea de log: podía llevar meses caído en verde, que es
    el falso verde que esta tarea existe para evitar."""
    from tasks import watchlist_tasks

    def _reventar():
        raise RuntimeError("Core inaccesible")

    monkeypatch.setattr(watchlist_tasks, "_check_health_async", lambda: _reventar())

    with pytest.raises(RuntimeError, match="Core inaccesible"):
        watchlist_tasks.check_watchlist_health()
