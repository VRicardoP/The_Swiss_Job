"""Regresiones de la auditoría G1 — scrapers varios.

- P3-7: irishjobs — la identidad del early-stop (URL por host) no coincidía
  con la del dedupe (id de plataforma): las ofertas compartidas descartadas
  nunca entraban en el cursor y el segundo host se re-crawleaba a
  presupuesto completo cada run.
- P3-8: schuljobs — int()/get() sin blindaje en el scroll AJAX tiraban el
  run entero perdiendo los detalles ya descargados.
- P3-10: NAE/Inspired — el bucle de N colegios sobre el MISMO host llamaba
  _report_block N veces → kill-switch (threshold 3) en un solo run.
"""

from unittest.mock import AsyncMock, patch

import pytest

from scrapers.irishjobs import IrishJobsScraper
from scrapers.schuljobs import SchulJobsScraper
from scrapers.swiss_schools_nae import SwissSchoolsNAEScraper


class TestP37IdentidadIrishjobs:
    def test_identidad_por_id_de_plataforma(self):
        """El mismo anuncio en ambos hosts comparte identidad de cursor."""
        stub_irishjobs = {"id": 123456, "url": "https://www.irishjobs.ie/job/x-123456"}
        stub_jobsie = {"id": 123456, "url": "https://www.jobs.ie/job/x-123456"}
        assert IrishJobsScraper.job_identity(stub_irishjobs) == IrishJobsScraper.job_identity(
            stub_jobsie
        )

    def test_normalizado_conserva_la_misma_identidad(self):
        """El job persistido (normalize_job) produce la MISMA identidad que el
        stub — el cursor y el early-stop hablan el mismo idioma."""
        scraper = IrishJobsScraper()
        raw = {
            "id": 987,
            "title": "Primary Teacher",
            "company": "School",
            "url": "https://www.jobs.ie/job/primary-987",
            "description": "d",
            "location": "Dublin",
        }
        normalized = scraper.normalize_job(raw)
        assert IrishJobsScraper.job_identity(normalized) == IrishJobsScraper.job_identity(
            raw
        )
        assert IrishJobsScraper.job_identity(raw) == "irishjobs:987"

    def test_sin_id_cae_a_url(self):
        stub = {"url": "https://www.irishjobs.ie/job/y"}
        assert IrishJobsScraper.job_identity(stub) == stub["url"]


class TestP38SchuljobsBlindaje:
    def test_safe_int_nextpage(self):
        scraper = SchulJobsScraper()
        assert scraper._safe_int("") == 0
        assert scraper._safe_int("abc") == 0
        assert scraper._safe_int("3") == 3

    def test_scroll_json_lista_no_revienta(self):
        """Un 200 con JSON lista en el scroll no debe tirar el run — se
        verifica que el código lo guarda con isinstance antes del .get."""
        import inspect

        from scrapers import schuljobs

        src = inspect.getsource(schuljobs)
        assert "isinstance(data, dict)" in src


@pytest.mark.asyncio
class TestP310KillSwitchPorRun:
    async def test_un_bloqueo_corta_el_bucle_de_colegios(self):
        """G1/P3-10: si el primer colegio reporta bloqueo, NO se sigue con el
        resto (mismo host): un episodio transitorio no debe sumar N reportes
        y alcanzar el kill-switch en un solo run."""
        scraper = SwissSchoolsNAEScraper()
        n_schools = len(scraper._schools)
        assert n_schools >= 2, "el escenario exige varios colegios"

        calls = []

        async def _blocked_scrape(query):
            calls.append(1)
            # Simula el flujo base: el colegio devolvió 403 y reportó bloqueo.
            scraper._run_block_reported = True
            return []

        with patch(
            "services.scraper_engine.BaseScraper._scrape_with_httpx",
            new=AsyncMock(side_effect=_blocked_scrape),
        ):
            result = await scraper._scrape_with_httpx("")

        assert result == []
        assert len(calls) == 1, (
            f"tras el bloqueo no debe visitarse ningún colegio más "
            f"(se visitaron {len(calls)}/{n_schools})"
        )
