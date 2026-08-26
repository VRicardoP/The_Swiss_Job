"""G4 — familia de la CAPA HTTP (`utils/http.py`).

Los cuatro flancos que abrió el `follow_redirects=True` de G3/P1-3, más el
coste del backoff que añadió el mismo commit:

- **P2-4**: el `await asyncio.sleep(...)` del camino de status no-2xx cubría
  también los 520/521/522/524 de Cloudflare, que NO están en
  `DEFAULT_RETRY_STATUSES`. Cada petición pasaba de instantánea a 7 s, y
  `thehub` paga el helper UNA VEZ POR OFERTA en su bucle de detalles.
- **P2-7**: `httpx` resuelve el `Location` con `URL.join`, que DESCARTA la
  query de la base. Un `308` con `Location` relativo sin query colapsaba la
  paginación a la página 1 y la fuente salía `ok`.
- **P3-1**: `httpx.TooManyRedirects` deriva de `httpx.HTTPError`, así que el
  `except` de reintento lo repetía: 21 saltos × 4 intentos = 84 peticiones.
- **P3-2**: `httpx` retira `Authorization` al saltar de host, pero NO las
  cabeceras propietarias: la API key viajaba al host nuevo.
- **P3-3**: un 301/302/303 degrada el POST de `jooble` a GET y descarta el
  cuerpo (`keywords`/`location`/`page`), y el run salía `ok`.
"""

import httpx
import pytest

from utils import fetch_diagnostics as diag
from utils.http import fetch_with_retry

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """Sin esperas reales; se registran para poder medirlas."""
    waits: list[float] = []

    async def _no_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr("utils.http.asyncio.sleep", _no_sleep)
    return waits


def _mock_transport(monkeypatch, handler):
    original_init = httpx.AsyncClient.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)


class TestP24CosteDeUn52xDeCloudflare:
    async def test_un_520_no_pausa_ni_se_reintenta(self, monkeypatch, _fast_backoff):
        peticiones: list[str] = []

        def _handler(request):
            peticiones.append(str(request.url))
            return httpx.Response(520, text="cloudflare")

        _mock_transport(monkeypatch, _handler)
        diag.begin()
        async with httpx.AsyncClient() as client:
            data = await fetch_with_retry(client, "https://thehub.io/api/jobs/1")

        assert data is None
        assert len(peticiones) == 1, (
            "un 52x de Cloudflare se reintenta 4 veces con 7 s de pausa; en el "
            "bucle de detalles de thehub (46-75 ofertas) eso es 322-526 s "
            "contra un soft_time_limit de 540 s para los 20 providers"
        )
        assert _fast_backoff == []
        assert [i.status for i in diag.issues()] == [520]


class TestP27LaQuerySobreviveAlRedirect:
    async def test_un_location_relativo_no_pierde_la_paginacion(self, monkeypatch):
        vistas: list[httpx.URL] = []

        def _handler(request):
            vistas.append(request.url)
            if not request.url.host.startswith("api."):
                return httpx.Response(200, json={"items": [1, 2]})
            if request.headers.get("x-hop") is None and "redirected" not in str(
                request.url
            ):
                return httpx.Response(
                    308, headers={"Location": "/public/vacancy/redirected"}
                )
            return httpx.Response(200, json={"items": [1, 2]})

        _mock_transport(monkeypatch, _handler)
        diag.begin()
        async with httpx.AsyncClient() as client:
            data = await fetch_with_retry(
                client,
                "https://api.ostjob.ch/public/vacancy/search",
                params={"page": 3, "size": 20},
            )

        assert data == {"items": [1, 2]}
        final = vistas[-1]
        assert final.params.get("page") == "3", (
            "la petición seguida perdió la paginación: cada página pedida "
            f"devuelve la página 1 y la fuente sale `ok` — url final {final}"
        )
        assert final.params.get("size") == "20"

    async def test_un_location_con_query_propia_manda(self, monkeypatch):
        vistas: list[httpx.URL] = []

        def _handler(request):
            vistas.append(request.url)
            if request.url.path == "/search":
                return httpx.Response(
                    308, headers={"Location": "/v2/search?cursor=abc"}
                )
            return httpx.Response(200, json={"ok": True})

        _mock_transport(monkeypatch, _handler)
        diag.begin()
        async with httpx.AsyncClient() as client:
            await fetch_with_retry(
                client, "https://api.example.test/search", params={"page": 1}
            )

        assert vistas[-1].params.get("cursor") == "abc"


class TestP31BucleDeRedirects:
    async def test_una_cadena_infinita_no_multiplica_las_peticiones(
        self, monkeypatch
    ):
        peticiones: list[str] = []

        def _handler(request):
            peticiones.append(str(request.url))
            n = len(peticiones)
            return httpx.Response(302, headers={"Location": f"/hop/{n}"})

        _mock_transport(monkeypatch, _handler)
        diag.begin()
        async with httpx.AsyncClient() as client:
            data = await fetch_with_retry(client, "https://loop.example.test/start")

        assert data is None
        assert len(peticiones) <= 4, (
            f"{len(peticiones)} peticiones: el tope de saltos no se respeta y "
            "el bucle de reintentos lo multiplica"
        )
        assert diag.issues(), "un bucle de redirects tiene que dejar rastro"


class TestP32LaApiKeyNoCruzaDeHost:
    async def test_las_cabeceras_propietarias_se_retiran_al_cambiar_de_host(
        self, monkeypatch
    ):
        recibidas: list[tuple[str, str | None]] = []

        def _handler(request):
            recibidas.append(
                (request.url.host, request.headers.get("x-rapidapi-key"))
            )
            if request.url.host == "jsearch.p.rapidapi.com":
                return httpx.Response(
                    302, headers={"Location": "https://evil.example/collect"}
                )
            return httpx.Response(200, json={"data": []})

        _mock_transport(monkeypatch, _handler)
        diag.begin()
        async with httpx.AsyncClient() as client:
            await fetch_with_retry(
                client,
                "https://jsearch.p.rapidapi.com/search",
                headers={"x-rapidapi-key": "SECRETO123", "accept": "application/json"},
            )

        assert recibidas[0] == ("jsearch.p.rapidapi.com", "SECRETO123")
        assert recibidas[-1][0] == "evil.example"
        assert recibidas[-1][1] is None, (
            "la API key viajó a otro host: httpx retira Authorization pero no "
            "las cabeceras propietarias"
        )


class TestP33ElPostNoSeDegradaAGet:
    @pytest.mark.parametrize("status", [301, 302, 303])
    async def test_un_3xx_que_no_preserva_el_metodo_no_se_sigue(
        self, monkeypatch, status
    ):
        metodos: list[tuple[str, int]] = []

        def _handler(request):
            body = request.content or b""
            metodos.append((request.method, len(body)))
            if request.url.path == "/api/search":
                return httpx.Response(status, headers={"Location": "/api/v2/search"})
            return httpx.Response(200, json={"jobs": []})

        _mock_transport(monkeypatch, _handler)
        diag.begin()
        async with httpx.AsyncClient() as client:
            data = await fetch_with_retry(
                client,
                "https://jooble.org/api/search",
                method="POST",
                json_body={"keywords": "lehrer", "location": "Bern", "page": 1},
            )

        assert data is None, (
            "el POST se degradó a GET y el cuerpo (keywords/location/page) se "
            "perdió, pero el run salía `ok`"
        )
        assert [m for m, _ in metodos] == ["POST"]
        assert diag.issues(), "el fallo tiene que quedar registrado"

    @pytest.mark.parametrize("status", [307, 308])
    async def test_los_que_si_preservan_el_metodo_se_siguen(self, monkeypatch, status):
        metodos: list[tuple[str, int]] = []

        def _handler(request):
            metodos.append((request.method, len(request.content or b"")))
            if request.url.path == "/api/search":
                return httpx.Response(status, headers={"Location": "/api/v2/search"})
            return httpx.Response(200, json={"jobs": [1]})

        _mock_transport(monkeypatch, _handler)
        diag.begin()
        async with httpx.AsyncClient() as client:
            data = await fetch_with_retry(
                client,
                "https://jooble.org/api/search",
                method="POST",
                json_body={"keywords": "lehrer"},
            )

        assert data == {"jobs": [1]}
        assert [m for m, _ in metodos] == ["POST", "POST"]
        assert metodos[1][1] > 0, "el cuerpo no viajó en el salto"
