"""G3 — LOTE F: tareas de cosecha y motor de scraping.

- P2-10: el aviso de soft time limit (`SoftTimeLimitExceeded`, que hereda de
  `Exception`) lo tragaban los `except Exception` de `fetch_scrapers` y
  `fetch_providers`. Se contaba como un error más de la oferta y el bucle
  seguía hasta que el límite DURO mataba el worker por SIGKILL: la cadena
  diaria se quedaba sin embeddings, sin dedup, sin matching y sin digest. Y la
  fuente cortada a mitad quedaba registrada como `record_storage(attempted, 0)`
  ⇒ «FUENTE DEGRADADA» falsa a los dos runs lentos.
P3-6 y P3-7 quedan APARCADOS (ver informe del lote): sus remedios prescritos
introducen regresiones peores que el defecto — corrupción de identidad /
pérdida de contenido en P3-6, y una alarma permanente sobre los colegios de la
watchlist (cuyo estado NORMAL es 0 vacantes) en P3-7.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select

from config import settings
from models.source_health import SourceHealth
from services.data_normalizer import DataNormalizer
from services.job_service import BaseJobProvider
from tasks.fetch_tasks import _fetch_providers_async
from tasks.scraping_tasks import _fetch_scrapers_async

# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


def _sample_job(title: str, url: str, source: str) -> dict:
    """Oferta mínima válida tal como la emite un scraper/provider."""
    return {
        "hash": f"h_{title}_{url}"[:32].ljust(32, "0"),
        "source": source,
        "title": title,
        "company": "Acme",
        "url": url,
        "location": "Bern",
        "canton": "BE",
        "description": "Stelle mit Verantwortung in einer Schule in Bern.",
        "description_snippet": "Stelle mit...",
        "remote": False,
        "tags": ["administration"],
        "logo": None,
        "salary_min_chf": None,
        "salary_max_chf": None,
        "salary_original": None,
        "salary_currency": None,
        "salary_period": None,
        "language": None,
        "seniority": None,
        "contract_type": None,
        "employment_type": None,
    }


def _make_mock_scraper(source_name: str, jobs: list[dict]):
    scraper = MagicMock()
    scraper.get_source_name.return_value = source_name
    scraper.fetch_jobs = AsyncMock(return_value=jobs)
    scraper.job_identity = BaseJobProvider.job_identity
    scraper.PAGE_SIZE = 20
    scraper.MAX_PAGES = 5
    return scraper


def _make_mock_provider(source_name: str, jobs: list[dict]):
    provider = MagicMock()
    provider.get_source_name.return_value = source_name
    provider.fetch_jobs = AsyncMock(return_value=jobs)
    return provider


def _mock_session_factory(db_session):
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


def _normalize_that_dies_on(nth: int):
    """DataNormalizer.normalize que lanza el soft time limit en la llamada `nth`.

    El aviso de Celery se emite UNA SOLA VEZ, en medio del procesamiento: este
    doble reproduce exactamente eso.
    """
    calls: list[dict] = []
    real = DataNormalizer.normalize

    def _normalize(job: dict) -> dict:
        calls.append(job)
        if len(calls) == nth:
            raise SoftTimeLimitExceeded()
        return real(job)

    return _normalize, calls


async def _health(db_session, source_key: str) -> SourceHealth | None:
    return (
        await db_session.execute(
            select(SourceHealth).where(SourceHealth.source_key == source_key)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# P2-10 — scrapers
# ---------------------------------------------------------------------------


class TestSoftTimeLimitEnScrapers:
    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_soft_limit_en_una_oferta_devuelve_cosecha_parcial(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """G3/P2-10: el aviso llega procesando la 2ª de 3 ofertas.

        La tarea debe TERMINAR devolviendo el summary acumulado y sin seguir
        procesando ni el resto de ofertas ni las fuentes siguientes (antes el
        bucle continuaba hasta el SIGKILL del límite duro).
        """
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", False)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", False)

        jobs = [
            _sample_job("Job A", "http://soft.test/a", "scr_soft"),
            _sample_job("Job B", "http://soft.test/b", "scr_soft"),
            _sample_job("Job C", "http://soft.test/c", "scr_soft"),
        ]
        lento = _make_mock_scraper("scr_soft", jobs)
        siguiente = _make_mock_scraper(
            "scr_next", [_sample_job("Job D", "http://soft.test/d", "scr_next")]
        )
        mock_scrapers.return_value = [lento, siguiente]

        normalize, calls = _normalize_that_dies_on(2)
        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            with patch(
                "tasks.scraping_tasks.DataNormalizer.normalize", side_effect=normalize
            ):
                summary = await _fetch_scrapers_async()

        assert summary["soft_time_limit"] is True, "el run no se declara parcial"
        assert len(calls) == 2, "el bucle de ofertas siguió tras el soft time limit"
        siguiente.fetch_jobs.assert_not_called()
        # El aviso de presupuesto NO es un error de la oferta.
        assert summary["errors"] == 0
        assert summary["scrapers"] == 0

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_soft_limit_tras_el_bucle_no_degrada_la_fuente(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """G3/P2-10: el aviso llega ya fuera del bucle de ofertas (antes del
        commit). La fuente se quedó sin tiempo, NO falló al guardar: registrar
        `record_storage(attempted, 0)` producía un «FUENTE DEGRADADA» falso a
        los dos runs lentos."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", False)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", False)

        jobs = [
            _sample_job("Job A", "http://post.test/a", "scr_post"),
            _sample_job("Job B", "http://post.test/b", "scr_post"),
        ]
        lento = _make_mock_scraper("scr_post", jobs)
        siguiente = _make_mock_scraper(
            "scr_after", [_sample_job("Job D", "http://post.test/d", "scr_after")]
        )
        mock_scrapers.return_value = [lento, siguiente]

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            with patch(
                "tasks.scraping_tasks.harvest_window.log_window_summary",
                side_effect=SoftTimeLimitExceeded(),
            ):
                summary = await _fetch_scrapers_async()

        assert summary["soft_time_limit"] is True
        siguiente.fetch_jobs.assert_not_called()
        fila = await _health(db_session, "scr_post")
        assert fila is not None
        assert fila.consecutive_unstored == 0, "FUENTE DEGRADADA falsa por un run lento"


# ---------------------------------------------------------------------------
# P2-10 — providers
# ---------------------------------------------------------------------------


class TestSoftTimeLimitEnProviders:
    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_soft_limit_persistiendo_devuelve_cosecha_parcial(
        self, mock_providers, db_session
    ):
        """G3/P2-10: mismo escenario en la fase 2 de `fetch_providers`."""
        jobs = [
            _sample_job("Dev A", "http://prov.test/a", "prov_soft"),
            _sample_job("Dev B", "http://prov.test/b", "prov_soft"),
            _sample_job("Dev C", "http://prov.test/c", "prov_soft"),
        ]
        mock_providers.return_value = [
            _make_mock_provider("prov_soft", jobs),
            _make_mock_provider(
                "prov_next", [_sample_job("Dev D", "http://prov.test/d", "prov_next")]
            ),
        ]

        normalize, calls = _normalize_that_dies_on(2)
        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            with patch(
                "tasks.fetch_tasks.DataNormalizer.normalize", side_effect=normalize
            ):
                summary = await _fetch_providers_async()

        assert summary["soft_time_limit"] is True
        assert len(calls) == 2, "el bucle de ofertas siguió tras el soft time limit"
        assert summary["providers"] == 0
        assert summary["errors"] == 0
        # La fuente cortada no puede quedar marcada como «no guarda nada».
        fila = await _health(db_session, "prov_soft")
        assert fila is None or fila.consecutive_unstored == 0

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_soft_limit_descargando_no_culpa_al_provider(
        self, mock_providers, db_session
    ):
        """G3/P2-10: la fase 1 (descarga en paralelo) es donde se va casi todo
        el presupuesto. El aviso allí NO es un fallo de red del provider que lo
        recibe: colgarle un OUTCOME_ERROR es una alerta de salud falsa."""
        lento = _make_mock_provider("prov_fetch", [])
        lento.fetch_jobs = AsyncMock(side_effect=SoftTimeLimitExceeded())
        mock_providers.return_value = [lento]

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_providers_async()

        assert summary["soft_time_limit"] is True
        assert summary["fetch_failed"] == 0, "OUTCOME_ERROR falso por el time limit"
        assert await _health(db_session, "prov_fetch") is None
