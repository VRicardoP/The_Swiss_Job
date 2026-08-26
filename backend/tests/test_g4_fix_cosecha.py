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
from providers.arbeitnow import ArbeitnowProvider  # noqa: E402
from providers.careerjet import CareerjetProvider  # noqa: E402
from providers.jobgether import JobgetherProvider  # noqa: E402
from providers.jooble import JoobleProvider  # noqa: E402
from providers.jsearch import JSearchProvider  # noqa: E402
from providers.nav_arbeidsplassen import NavArbeidsplassenProvider  # noqa: E402
from providers.ostjob import OstjobProvider  # noqa: E402
from providers.remotive import RemotiveProvider  # noqa: E402
from providers.workingnomads import WorkingNomadsProvider  # noqa: E402
from providers.zentraljob import ZentraljobProvider  # noqa: E402
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
    (ArbeitnowProvider, {}),
    (CareerjetProvider, {}),
    (CareerjetProvider, []),
    (JobgetherProvider, {}),
    (JoobleProvider, {}),
    (JSearchProvider, {}),
    (NavArbeidsplassenProvider, {}),
    (OstjobProvider, {}),
    (OstjobProvider, {"renombrado": []}),
    (RemotiveProvider, {}),
    (WorkingNomadsProvider, {}),
    (ZentraljobProvider, {}),
    (ZentraljobProvider, {"renombrado": []}),
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


@pytest.mark.asyncio
async def test_un_feed_legitimamente_vacio_sigue_saliendo_empty(monkeypatch):
    """Cota: la estructura CORRECTA sin resultados no puede dar falso positivo."""
    _mock_transport(monkeypatch, {"items": []})
    diag.begin()
    jobs = await OstjobProvider().fetch_jobs("", "Switzerland")

    assert jobs == []
    assert diag.classify(len(jobs), diag.issues()) == "empty"


# ---------------------------------------------------------------------------
# P3-4 / P3-5 — irishjobs: las DOS señales del run de dos hosts
# ---------------------------------------------------------------------------

from tests.test_g3_fix_flancos_gemelos import (  # noqa: E402
    _client_factory,
    _identities,
    _irish_scraper,
    _page,
)
from unittest.mock import AsyncMock, MagicMock  # noqa: E402


class TestP34LaSenalConHambreSobreviveAlSegundoHost:
    """`_stop_reason` tiene TRES estados y cada uno lo consume un guard
    distinto. El acumulador `had_error` solo conservaba uno."""

    @pytest.mark.asyncio
    async def test_el_known_page_del_host_2_no_borra_el_hambre_del_host_1(
        self, monkeypatch
    ):
        scraper = _irish_scraper()
        # Presupuesto de 1 página: el host 1 lo agota SIN early-stop — se queda
        # «con hambre» (`_stop_reason is None`), que es lo que dispara el
        # re-bootstrap de scraping_tasks.
        monkeypatch.setattr(scraper, "_max_pages_this_run", 1)
        # El cursor conoce entero el host 2 → su página 1 es `known_page`.
        scraper._known_urls = set(_identities(101))

        client = MagicMock()
        client.get = AsyncMock(
            side_effect=[_page("ie", first_id=1), _page("js", first_id=101)]
        )
        diag.begin()
        with patch("scrapers.irishjobs.httpx.AsyncClient", _client_factory(client)):
            await scraper._scrape_with_httpx("")

        assert scraper._stop_reason is None, (
            "el `known_page` del host 2 borró el «con hambre» del host 1: el "
            "re-bootstrap del presupuesto (ebb2c51) no se dispara y las "
            "ofertas hundidas bajo el horizonte no se descargan nunca"
        )

    @pytest.mark.asyncio
    async def test_el_error_sigue_ganando_a_todo(self, monkeypatch):
        """No-regresión de G3/P2-1: el `error` mantiene la máxima prioridad."""
        scraper = _irish_scraper()
        scraper._known_urls = set(_identities(101))
        client = MagicMock()
        client.get = AsyncMock(
            side_effect=[
                _page("ie", first_id=1),
                httpx.ConnectTimeout("boom"),
                _page("js", first_id=101),
            ]
        )
        diag.begin()
        with patch("scrapers.irishjobs.httpx.AsyncClient", _client_factory(client)):
            await scraper._scrape_with_httpx("")

        assert scraper._stop_reason == "error"

    def test_la_prioridad_esta_declarada(self):
        from services.scraper_engine import BaseScraper

        combinar = BaseScraper.combine_stop_reasons
        assert combinar(["error", None, "known_page"]) == "error"
        assert combinar([None, "known_page"]) is None
        assert combinar(["known_page", "known_page"]) == "known_page"
        assert combinar([]) is None


class TestP35UnBloqueoCortaElRun:
    @pytest.mark.asyncio
    async def test_los_dos_hosts_no_suman_dos_bloqueos_en_un_run(self):
        """Los dos hosts comparten `SOURCE_NAME`: sin el corte, el kill-switch
        de 3 bloques salta en 2 runs en vez de 3."""
        scraper = _irish_scraper()
        reportes: list[str] = []

        async def _harvest_bloqueado(client, host, seen_ids):
            """Host que topa con un challenge y reporta bloqueo a compliance."""
            reportes.append(host)
            scraper._run_block_reported = True
            return []

        with (
            patch.object(scraper, "_harvest_host", _harvest_bloqueado),
            patch("scrapers.irishjobs.httpx.AsyncClient", _client_factory(MagicMock())),
        ):
            await scraper._scrape_with_httpx("")

        assert len(reportes) == 1, (
            f"{len(reportes)} bloqueos reportados en UN run: el kill-switch "
            "(threshold 3) saltaría en 2 runs en vez de 3"
        )
