"""G4 — familia de la COSECHA.

- **P2-8**: los dos `diag.record` de estructura que añadió G3 a `careerjet`
  quedaron DETRÁS del `if not data: break`, así que un 200 con `{}` o `[]` los
  esquivaba y la fuente volvía a salir `empty`. El barrido de los 13 providers
  JSON encontró 11 iguales — entre ellos `ostjob` y `zentraljob`, las dos que
  dan nombre al commit del 308: el día que su 308 se convierta en un 200 con
  cuerpo vacío, volverán a morir en silencio.
- **P3-4**: el acumulador `had_error` de `irishjobs` propaga el `error` pero
  BORRA la señal «con hambre» (`_stop_reason = None`), que es justo la que
  consume el guard de re-bootstrap que cerró `ebb2c51`.
- **P3-5**: los dos hosts de `irishjobs` comparten `SOURCE_NAME` y el bucle no
  recibió el corte `if self._run_block_reported: break` que NAE e Inspired sí
  tienen: el kill-switch de 3 bloques salta en 2 runs en vez de 3.
- **P2-2**: el pool prefork levanta `SoftTimeLimitExceeded` desde un SIGNAL
  HANDLER. Un run de cosecha pasa casi todo su tiempo bloqueado en
  `epoll_wait` (HTTP, Playwright, BD); si la señal llega ahí, la excepción se
  levanta dentro de `selectors.EpollSelector.select()` —FUERA del árbol de
  corrutinas— y ESCAPA de `asyncio.run()`. Ninguno de los cuatro handlers
  internos que añadió `f073d92` la ve: caía en el `except Exception` del
  wrapper → `self.retry(...)` → se reintentaba la cosecha ENTERA con el mismo
  presupuesto que ya no alcanzó, y al agotar los reintentos la cadena
  `daily_harvest` abortaba por `link_error` (ni embeddings, ni dedup, ni
  matching, ni digest ese día). El test del commit ejercita el escenario
  contrario (la señal llega durante CPU, dentro de la corrutina).
"""

from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded

from tasks.fetch_tasks import fetch_providers
from tasks.scraping_tasks import fetch_scrapers


class TestP22ElAvisoQueEscapaDeAsyncioRun:
    """La señal llega con el loop en `epoll_wait`: `asyncio.run()` la propaga
    tal cual al wrapper síncrono. Se simula exactamente eso."""

    def test_fetch_providers_no_reintenta_la_cosecha_entera(self):
        with patch(
            "tasks.fetch_tasks.asyncio.run", side_effect=SoftTimeLimitExceeded()
        ):
            resultado = fetch_providers.apply()

        assert resultado.successful(), (
            "el aviso de presupuesto acabó en `self.retry`: se re-ejecuta la "
            "cosecha entera con el mismo presupuesto que ya no alcanzó y, al "
            "agotar los reintentos, la cadena daily_harvest aborta"
        )
        assert resultado.result["soft_time_limit"] is True

    def test_fetch_scrapers_no_reintenta_la_cosecha_entera(self):
        with patch(
            "tasks.scraping_tasks.asyncio.run", side_effect=SoftTimeLimitExceeded()
        ):
            resultado = fetch_scrapers.apply()

        assert resultado.successful()
        assert resultado.result["soft_time_limit"] is True

    def test_un_error_normal_sigue_reintentandose(self):
        """Cota: solo el aviso de presupuesto se exceptúa del retry."""
        with patch(
            "tasks.fetch_tasks.asyncio.run", side_effect=RuntimeError("BD caída")
        ):
            resultado = fetch_providers.apply()

        assert not resultado.successful()


# ---------------------------------------------------------------------------
# P2-8 — un 200 ilegible NO es «no hay ofertas»
# ---------------------------------------------------------------------------

import httpx  # noqa: E402
import pytest  # noqa: E402

from providers.adzuna import AdzunaProvider  # noqa: E402
from providers.careerjet import CareerjetProvider  # noqa: E402
from providers.jooble import JoobleProvider  # noqa: E402
from providers.jsearch import JSearchProvider  # noqa: E402
from config import settings  # noqa: E402
from utils import fetch_diagnostics as diag  # noqa: E402


def _mock_transport(monkeypatch, body):
    original_init = httpx.AsyncClient.__init__

    def _handler(request):
        return httpx.Response(200, json=body)

    def _patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)


@pytest.fixture
def _credenciales(monkeypatch):
    """Los providers gated necesitan credencial para llegar al HTTP."""
    for name, value in (
        ("ADZUNA_APP_ID", "id"),
        ("ADZUNA_APP_KEY", "key"),
        ("CAREERJET_AFFID", "affid"),
        ("JOOBLE_API_KEY", "key"),
        ("JSEARCH_RAPIDAPI_KEY", "key"),
    ):
        if hasattr(settings, name):
            monkeypatch.setattr(settings, name, value)


# (provider, cuerpo 200 ilegible)
_PROVIDERS_ILEGIBLES = [
    (AdzunaProvider, {}),
    (CareerjetProvider, {}),
    (CareerjetProvider, []),
    (JoobleProvider, {}),
    (JSearchProvider, {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_cls,body", _PROVIDERS_ILEGIBLES)
async def test_un_200_ilegible_sale_error_y_no_empty(
    monkeypatch, _credenciales, provider_cls, body
):
    _mock_transport(monkeypatch, body)
    diag.begin()
    jobs = await provider_cls().fetch_jobs("", "Switzerland")

    assert jobs == []
    issues = diag.issues()
    assert issues, (
        f"{provider_cls.__name__}: un HTTP 200 con {body!r} sale `empty` — "
        "indistinguible de un feed legítimamente vacío, que es la clase V.0"
    )
    assert diag.classify(len(jobs), issues) == "error"
