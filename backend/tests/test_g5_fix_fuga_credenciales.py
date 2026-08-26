"""G5/P2-1 — la API key de jooble llegaba a la columna de salud y a la alerta.

`fa5d493` detectó el riesgo y lo escribió en el código («la url lleva la API key
incrustada y `diag` acaba en la columna de salud, que el panel muestra»), pero
redactó UNA sola de las cuatro salidas: la de `diag.json_items`, que es el caso
RARO (200 con estructura ilegible). `utils/http.py` registraba por su cuenta,
con la URL real, en sus tres caminos de fallo —403, 429 y fallo de parseo—, que
son los PROBABLES: key revocada, rate limit, timeout. Destino:
`SourceHealth.last_error_detail` (`String(500)`, la columna que muestra el
panel) y el texto de la alerta «N runs seguidos con error — último: …».

El 403 es el caso perverso: la key se publica en el panel justo el día que
caduca o la revocan.
"""

import httpx
import pytest

from providers.jooble import JoobleProvider, _REDACTED_URL
from utils import fetch_diagnostics as diag

_KEY = "K3Y-53CR3T4-D3-J00BL3"


def _transport(handler):
    return httpx.MockTransport(handler)


async def _cosechar(monkeypatch, handler, caplog) -> tuple[list, str]:
    """Corre `fetch_jobs` de jooble contra un transporte simulado.

    Devuelve (issues registrados, logs) — las dos superficies donde la
    credencial se publicaba.
    """
    from config import settings

    monkeypatch.setattr(settings, "JOOBLE_API_KEY", _KEY)

    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = _transport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)

    diag.begin()
    provider = JoobleProvider()
    # Sin pausas entre reintentos: el test no mide tiempo.
    monkeypatch.setattr("utils.http.asyncio.sleep", _no_sleep)
    with caplog.at_level("WARNING"):
        await provider.fetch_jobs("python")
    return diag.issues(), caplog.text


async def _no_sleep(_delay):
    return None


def _detalles(issues) -> str:
    return " | ".join(
        str(getattr(i, "detail", "") or "") + str(getattr(i, "url", "") or "")
        for i in issues
    )


@pytest.mark.asyncio
class TestLaKeyDeJoobleNoSaleDeCasa:
    async def test_403_key_revocada(self, monkeypatch, caplog):
        def handler(request):
            return httpx.Response(403, text="forbidden")

        issues, logs = await _cosechar(monkeypatch, handler, caplog)

        assert issues, "un 403 debe registrar issue (si no, la fuente sale `empty`)"
        assert _KEY not in _detalles(issues), (
            "la API key viaja a source_health.last_error_detail y al panel"
        )
        assert _KEY not in logs, "la API key queda en los logs del backend"
        assert any(_REDACTED_URL in str(getattr(i, "url", "")) for i in issues)

    async def test_429_rate_limit_agotando_reintentos(self, monkeypatch, caplog):
        def handler(request):
            return httpx.Response(429, text="slow down")

        issues, logs = await _cosechar(monkeypatch, handler, caplog)

        assert _KEY not in _detalles(issues)
        assert _KEY not in logs

    async def test_200_con_cuerpo_ilegible(self, monkeypatch, caplog):
        def handler(request):
            return httpx.Response(200, text="<html>not json</html>")

        issues, logs = await _cosechar(monkeypatch, handler, caplog)

        assert issues
        assert _KEY not in _detalles(issues)
        assert _KEY not in logs

    async def test_timeout_agotando_reintentos(self, monkeypatch, caplog):
        def handler(request):
            raise httpx.ConnectTimeout("timed out")

        issues, logs = await _cosechar(monkeypatch, handler, caplog)

        assert issues
        assert _KEY not in _detalles(issues)
        assert _KEY not in logs


@pytest.mark.asyncio
class TestDiagUrlNoCambiaLaPeticion:
    async def test_la_peticion_sigue_yendo_a_la_url_real(self, monkeypatch, caplog):
        """`diag_url` redacta lo que se PUBLICA, no adónde se pide."""
        vistas: list[str] = []

        def handler(request):
            vistas.append(str(request.url))
            return httpx.Response(200, json={"jobs": [], "totalCount": 0})

        await _cosechar(monkeypatch, handler, caplog)

        assert vistas and all(_KEY in u for u in vistas), (
            "la redacción no puede alterar la URL de la petición real"
        )
