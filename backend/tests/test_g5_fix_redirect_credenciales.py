"""G5/P3-1 — la query (y con ella la credencial) no cruza de host en un redirect.

`_headers_for_host` sí discrimina por host: en un salto cross-host retira todas
las cabeceras propietarias (`x-rapidapi-key`, `x-api-key`…), que fue el fix
G4/P3-2. Pero `_resolve_redirect` copiaba la query SIEMPRE, incluido ese mismo
salto, así que la credencial se reabría por la otra puerta: `app_id`+`app_key`
de adzuna, `affid` de careerjet, o el token de cualquier feed RSS.

El canal no existía antes: con `follow_redirects=True` httpx resuelve el
`Location` con `URL.join`, que DESCARTA la query. Lo abrió el seguimiento
manual que añadió G4/P2-7 para conservar `page`/`size` en los saltos relativos
— conservarla dentro del mismo host es correcto; cruzando de host, no.
"""

import httpx
import pytest

from utils.http import fetch_rss, fetch_with_retry

_APP_KEY = "SECRETKEY456"
_TOKEN = "SECRETTOKEN"


def _redirector(location: str, capturadas: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        capturadas.append(str(request.url))
        if len(capturadas) == 1:
            return httpx.Response(302, headers={"location": location})
        return httpx.Response(200, json={"ok": True})

    return handler


@pytest.mark.asyncio
class TestLaQueryNoCruzaDeHost:
    async def test_salto_cross_host_descarta_la_query_con_la_credencial(self):
        vistas: list[str] = []
        transport = httpx.MockTransport(
            _redirector("https://cdn.evil-partner.example/landing", vistas)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_with_retry(
                client,
                "https://api.adzuna.com/v1/api/jobs/ch/search/1",
                params={"app_id": "APPID123", "app_key": _APP_KEY, "what": "python"},
            )

        assert len(vistas) == 2
        assert _APP_KEY in vistas[0], "la petición original sí debe llevar la key"
        assert _APP_KEY not in vistas[1], (
            "la app_key de adzuna viaja entera a otro host en el redirect"
        )
        assert "evil-partner" in vistas[1]

    async def test_salto_al_MISMO_host_conserva_la_query(self):
        """No-regresión de G4/P2-7: `page`/`size` deben sobrevivir."""
        vistas: list[str] = []
        transport = httpx.MockTransport(_redirector("/public/vacancy/final", vistas))
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_with_retry(
                client,
                "https://portal.example/public/vacancy/search",
                params={"page": 3, "size": 20},
            )

        assert len(vistas) == 2
        assert "page=3" in vistas[1] and "size=20" in vistas[1]

    async def test_location_absoluto_al_mismo_host_conserva_la_query(self):
        vistas: list[str] = []
        transport = httpx.MockTransport(
            _redirector("https://portal.example/public/vacancy/final", vistas)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_with_retry(
                client,
                "https://portal.example/public/vacancy/search",
                params={"page": 3},
            )

        assert "page=3" in vistas[1]

    async def test_fetch_rss_hereda_la_misma_garantia(self):
        vistas: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            vistas.append(str(request.url))
            if len(vistas) == 1:
                return httpx.Response(
                    301, headers={"location": "https://tracker.example/moved"}
                )
            return httpx.Response(200, text="<rss/>")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await fetch_rss(client, f"https://feed.example/rss?token={_TOKEN}")

        assert len(vistas) == 2
        assert _TOKEN in vistas[0]
        assert _TOKEN not in vistas[1], "el token del feed cruza de host"
