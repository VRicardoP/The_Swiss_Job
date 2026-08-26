"""Regresiones de la auditoría G2 — el guard del cursor en los bucles PROPIOS.

El fix G1/P2-4 marcó `_stop_reason = "error"` en los cortes por fallo del
MOTOR base, pero los scrapers que sobreescriben `_scrape_with_httpx` con su
propia paginación quedaron fuera:

- P2-1: irishjobs (`_harvest_host`, 2 hosts StepStone) — un run que cosecha
  la página 1 y cae en la 2 salía `_stop_reason=None`, el guard de
  `scraping_tasks` dejaba avanzar el cursor y el run siguiente hacía
  early-stop en la página 1: las ofertas de la página 2+ (newest-first) no se
  descargaban JAMÁS.
- P2-2: schuljobs (scroll AJAX) — mismo flanco: con la página inicial ya
  cosechada, el fallo a mitad de scroll dejaba que el cursor la aprendiera y
  el run siguiente no lanzaba ni una petición de scroll (380+ ofertas viven
  ahí).
- P3-1: NAE/Inspired — las N pasadas por colegio comparten `_stop_reason`:
  el "error" parcial del colegio 1 lo borraba el "known_page" del colegio 2.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scrapers.irishjobs import IrishJobsScraper
from scrapers.schuljobs import SchulJobsScraper
from scrapers.swiss_schools_inspired import SwissSchoolsInspiredScraper
from scrapers.swiss_schools_nae import SwissSchoolsNAEScraper
from utils import fetch_diagnostics as diag


def _irish_page(n_items: int, first_id: int = 1) -> MagicMock:
    """Respuesta de listado StepStone con `n_items` ofertas parseables."""
    items = [
        {
            "id": first_id + i,
            "title": f"Teacher {first_id + i}",
            "url": f"/job/t{first_id + i}",
        }
        for i in range(n_items)
    ]
    blob = json.dumps({"searchResults": {"items": items}})
    resp = MagicMock()
    resp.status_code = 200
    resp.text = (
        "<html><head><script>"
        f'window.__PRELOADED_STATE__["app-unifiedResultlist"] = {blob};'
        "</script></head><body></body></html>"
    )
    return resp


def _irish_scraper() -> IrishJobsScraper:
    scraper = IrishJobsScraper()
    scraper.RATE_LIMIT_SECONDS = 0.0
    scraper.MAX_RETRIES = 0
    scraper.RETRY_BACKOFF_SECONDS = 0.0
    return scraper


@pytest.mark.asyncio
class TestP21IrishjobsRunConHambre:
    async def test_error_de_red_en_la_pagina_2_marca_el_run(self):
        """Cosecha parcial + ConnectTimeout → `_stop_reason='error'`."""
        scraper = _irish_scraper()
        client = MagicMock()
        client.get = AsyncMock(
            side_effect=[_irish_page(25), httpx.ConnectTimeout("boom")]
        )
        diag.begin()

        stubs = await scraper._harvest_host(client, "https://www.irishjobs.ie", set())

        assert len(stubs) == 25, "la página 1 sí se cosechó (degradación parcial)"
        assert scraper._stop_reason == "error", (
            "el run terminó «con hambre»: el cursor NO debe aprender la página 1"
        )

    async def test_estado_no_200_en_la_pagina_2_marca_el_run(self):
        """Fin de listado por HTTP de error ≠ fin de listado real."""
        scraper = _irish_scraper()
        bad = MagicMock()
        bad.status_code = 500
        bad.text = ""
        client = MagicMock()
        client.get = AsyncMock(side_effect=[_irish_page(25), bad])
        diag.begin()

        await scraper._harvest_host(client, "https://www.irishjobs.ie", set())

        assert scraper._stop_reason == "error"

    async def test_blob_indescifrable_en_la_pagina_2_marca_el_run(self):
        """Redeploy/HTML inesperado: tampoco es fin de listado."""
        scraper = _irish_scraper()
        broken = MagicMock()
        broken.status_code = 200
        broken.text = "<html><body>sin blob</body></html>"
        client = MagicMock()
        client.get = AsyncMock(side_effect=[_irish_page(25), broken])
        diag.begin()

        await scraper._harvest_host(client, "https://www.irishjobs.ie", set())

        assert scraper._stop_reason == "error"

    async def test_fin_de_listado_limpio_no_marca_error(self):
        """No-regresión: la página incompleta es fin de listado legítimo."""
        scraper = _irish_scraper()
        client = MagicMock()
        client.get = AsyncMock(side_effect=[_irish_page(3)])
        diag.begin()

        stubs = await scraper._harvest_host(client, "https://www.irishjobs.ie", set())

        assert len(stubs) == 3
        assert scraper._stop_reason is None


def _schuljobs_initial_html(n_jobs: int) -> str:
    cards = "".join(
        f'<div class="card"><h3><a class="js-joboffer-detail" '
        f'href="https://www.schuljobs.ch/job/{i}">Lehrperson {i}</a></h3>'
        f"<p>ZH · Zürich · Schule {i}</p></div>"
        for i in range(n_jobs)
    )
    return (
        "<html><body>"
        f'<div data-searchhash="abc123"></div><div data-nextpage="2"></div>'
        f"{cards}</body></html>"
    )


def _schuljobs_scraper() -> SchulJobsScraper:
    scraper = SchulJobsScraper()
    scraper.RATE_LIMIT_SECONDS = 0.0
    scraper.MAX_RETRIES = 0
    scraper.RETRY_BACKOFF_SECONDS = 0.0
    scraper.FETCH_DETAILS = False  # la fase 4 no interviene en este flanco
    return scraper


def _schuljobs_run(scraper, scroll_outcome):
    """Ejecuta el scraper con página inicial buena y `scroll_outcome` en el scroll."""
    initial = MagicMock()
    initial.status_code = 200
    initial.text = _schuljobs_initial_html(20)

    async def _fake_request(do_request, url: str = ""):
        if not calls:
            calls.append("initial")
            return initial
        calls.append("scroll")
        if isinstance(scroll_outcome, Exception):
            raise scroll_outcome
        return scroll_outcome

    calls: list[str] = []
    scraper._request_with_retry = _fake_request
    return calls


@pytest.mark.asyncio
class TestP22SchuljobsScrollConHambre:
    async def test_error_de_red_en_el_scroll_marca_el_run(self):
        scraper = _schuljobs_scraper()
        calls = _schuljobs_run(scraper, httpx.ConnectTimeout("boom"))
        diag.begin()

        stubs = await scraper._scrape_with_httpx("")

        assert calls == ["initial", "scroll"]
        assert len(stubs) == 20, "la página inicial sí se cosechó"
        assert scraper._stop_reason == "error", (
            "fallo a mitad de scroll: el cursor NO debe aprender la página inicial"
        )

    async def test_scroll_bloqueado_marca_el_run(self):
        scraper = _schuljobs_scraper()
        blocked = MagicMock()
        blocked.status_code = 403
        blocked.text = ""
        calls = _schuljobs_run(scraper, blocked)
        diag.begin()

        with patch.object(SchulJobsScraper, "_report_block", new=AsyncMock()):
            await scraper._scrape_with_httpx("")

        assert calls == ["initial", "scroll"]
        assert scraper._stop_reason == "error"

    async def test_scroll_con_json_ilegible_marca_el_run(self):
        scraper = _schuljobs_scraper()
        garbage = MagicMock()
        garbage.status_code = 200
        garbage.json = MagicMock(side_effect=ValueError("no json"))
        calls = _schuljobs_run(scraper, garbage)
        diag.begin()

        await scraper._scrape_with_httpx("")

        assert calls == ["initial", "scroll"]
        assert scraper._stop_reason == "error"

    async def test_scroll_agotado_limpiamente_no_marca_error(self):
        """No-regresión: el scroll que devuelve una página corta es fin real."""
        scraper = _schuljobs_scraper()
        last = MagicMock()
        last.status_code = 200
        last.json = MagicMock(
            return_value={"html": _schuljobs_initial_html(2), "nextpage": 3}
        )
        calls = _schuljobs_run(scraper, last)
        diag.begin()

        stubs = await scraper._scrape_with_httpx("")

        assert calls == ["initial", "scroll"]
        assert len(stubs) == 20  # el fragmento repite URLs: dedupe por URL
        assert scraper._stop_reason is None


@pytest.mark.asyncio
class TestP31ErrorEntreColegios:
    @pytest.mark.parametrize(
        "scraper_cls", [SwissSchoolsNAEScraper, SwissSchoolsInspiredScraper]
    )
    async def test_el_error_del_primer_colegio_sobrevive_al_early_stop_del_segundo(
        self, scraper_cls
    ):
        """El "known_page" del colegio 2 borraba el "error" del colegio 1."""
        scraper = scraper_cls()
        assert len(scraper._schools) >= 2, "el escenario exige varios colegios"
        outcomes = iter(["error", "known_page"])

        async def _fake_pass(query):
            scraper._stop_reason = next(outcomes, "known_page")
            return []

        with patch(
            "services.scraper_engine.BaseScraper._scrape_with_httpx",
            new=AsyncMock(side_effect=_fake_pass),
        ):
            await scraper._scrape_with_httpx("")

        assert scraper._stop_reason == "error", (
            "el error parcial de cualquier pasada debe ganar: el cursor no "
            "puede aprender identidades de un run con hambre"
        )

    async def test_run_sin_errores_conserva_su_motivo(self):
        """No-regresión: sin errores, el motivo del último colegio manda."""
        scraper = SwissSchoolsNAEScraper()

        async def _fake_pass(query):
            scraper._stop_reason = "known_page"
            return []

        with patch(
            "services.scraper_engine.BaseScraper._scrape_with_httpx",
            new=AsyncMock(side_effect=_fake_pass),
        ):
            await scraper._scrape_with_httpx("")

        assert scraper._stop_reason == "known_page"
