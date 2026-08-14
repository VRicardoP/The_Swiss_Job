"""Tests del pipeline de scrapers (VD.2 + VD.3) — fallos que se presentaban
como éxitos.

Reproducen el modo de fallo REAL de `stelle_admin`: una oferta cuyo INSERT
colisiona contra `ix_jobs_url` (URL ya ocupada por otro hash). Antes de VD.2
el cursor aprendía igualmente su identidad (envenenado para siempre) y antes
de VD.3 la fuente seguía marcada `ok` en `source_health`.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from config import settings
from models.source_cursor import SourceCursor
from models.source_health import SourceHealth
from services.job_repository import JobRepository
from services.job_service import BaseJobProvider
from tasks.scraping_tasks import _fetch_scrapers_async


def _make_mock_scraper(source_name: str, jobs: list[dict]):
    """Scraper simulado con la superficie que usa el pipeline."""
    scraper = MagicMock()
    scraper.get_source_name.return_value = source_name
    scraper.fetch_jobs = AsyncMock(return_value=jobs)
    # Identidad real (estática): la misma que usan los scrapers de verdad.
    scraper.job_identity = BaseJobProvider.job_identity
    scraper.PAGE_SIZE = 20
    scraper.MAX_PAGES = 5
    return scraper


def _sample_job(title: str, company: str, url: str) -> dict:
    """Job mínimo válido tal como lo emite un scraper."""
    return {
        "hash": f"h_{title}_{company}_{url}"[:32].ljust(32, "0"),
        "source": "scr_test",
        "title": title,
        "company": company,
        "url": url,
        "location": "Bern",
        "canton": "BE",
        "description": "Interessante Stelle in der Bundesverwaltung in Bern.",
        "description_snippet": "Interessante Stelle...",
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


def _mock_session_factory(db_session):
    """Factoría de task_session que entrega la sesión de test."""

    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


async def _occupy_url(db_session, url: str) -> None:
    """Deja la URL ocupada por OTRA oferta (otro hash) ya commiteada, para que
    el siguiente INSERT con esa URL colisione contra ix_jobs_url."""
    repo = JobRepository(db_session)
    await repo.upsert_job(_sample_job("Occupant Job", "Other Corp", url))
    await db_session.commit()


class TestCursorSoloAprendeLoPersistido:
    """VD.2 — el cursor no puede aprender identidades que no se guardaron."""

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_guardado_fallido_no_entra_en_el_cursor(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """DoD VD.2: el job que colisiona NO entra en recent_identities (el
        siguiente run vuelve a verlo); el que sí se guardó, sí entra."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", True)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", False)

        await _occupy_url(db_session, "http://scr.test/taken")
        jobs = [
            _sample_job("Job OK", "Acme", "http://scr.test/ok"),
            # Mismo modo de fallo que stelle_admin: URL ocupada, hash distinto.
            _sample_job("Job Fail", "Acme", "http://scr.test/taken"),
        ]
        mock_scrapers.return_value = [_make_mock_scraper("scr_test", jobs)]

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_scrapers_async()

        assert summary["errors"] == 1
        cursor = (
            await db_session.execute(
                select(SourceCursor).where(SourceCursor.source_key == "scr_test")
            )
        ).scalar_one()
        assert "http://scr.test/ok" in cursor.recent_identities
        assert "http://scr.test/taken" not in cursor.recent_identities


class TestSaludDePersistencia:
    """VD.3 — descargar N>0 y guardar 0 debe acabar en fuente degradada."""

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_descarga_sin_guardar_acaba_en_unhealthy(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """DoD VD.3: dos runs seguidos con fetched>0 y stored==0 → la fuente
        aparece en summary["unhealthy"] y su racha queda registrada."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", False)
        monkeypatch.setattr(settings, "SOURCE_HEALTH_UNSTORED_STREAK", 2)

        await _occupy_url(db_session, "http://scr.test/clash")

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            # Cada run con su propio scraper/job: el pipeline muta el dict.
            mock_scrapers.return_value = [
                _make_mock_scraper(
                    "scr_test",
                    [_sample_job("Clash A", "Acme", "http://scr.test/clash")],
                )
            ]
            summary1 = await _fetch_scrapers_async()
            # Primer run: puede ser un hipo transitorio — todavía sin alerta.
            assert summary1["unhealthy"] == []

            mock_scrapers.return_value = [
                _make_mock_scraper(
                    "scr_test",
                    [_sample_job("Clash B", "Acme", "http://scr.test/clash")],
                )
            ]
            summary2 = await _fetch_scrapers_async()

        assert any(entry.startswith("scr_test:") for entry in summary2["unhealthy"])
        fila = (
            await db_session.execute(
                select(SourceHealth).where(SourceHealth.source_key == "scr_test")
            )
        ).scalar_one()
        assert fila.consecutive_unstored == 2
        assert fila.last_stored_count == 0
        # La señal de DESCARGA sigue intacta: la fuente descargó bien.
        assert fila.last_outcome == "ok"


class TestFalloDeLote:
    """F3 — perder el LOTE entero (commit/cursor fallidos tras los savepoints)
    también es señal de persistencia, no solo el fallo job a job."""

    @patch("tasks.scraping_tasks.CursorStore")
    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_fallo_del_lote_cuenta_para_la_racha(
        self, mock_scrapers, mock_store_cls, monkeypatch, db_session
    ):
        """El rollback externo pierde el lote entero: sin registrar la señal,
        `consecutive_unstored` quedaba congelado a 0 y la fuente jamás llegaba
        a degradada — el mismo "fallo que se presenta como éxito" de VD.3, un
        nivel más arriba."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", True)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", False)
        monkeypatch.setattr(settings, "SOURCE_HEALTH_UNSTORED_STREAK", 2)

        store = mock_store_cls.return_value
        store.load = AsyncMock(return_value=MagicMock())
        store.known_identities.return_value = set()
        # El cursor revienta DESPUÉS de los savepoints y ANTES del commit del
        # lote: es el fallo a nivel de lote que dispara el except externo.
        store.update_after_run.side_effect = RuntimeError("cursor corrupto")

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            for _ in range(2):
                mock_scrapers.return_value = [
                    _make_mock_scraper(
                        "scr_lote",
                        [_sample_job("Batch Job", "Acme", "http://scr.test/batch")],
                    )
                ]
                summary = await _fetch_scrapers_async()

        assert summary["errors"] >= 1
        assert any(entry.startswith("scr_lote:") for entry in summary["unhealthy"])
        fila = (
            await db_session.execute(
                select(SourceHealth).where(SourceHealth.source_key == "scr_lote")
            )
        ).scalar_one()
        assert fila.consecutive_unstored == 2
