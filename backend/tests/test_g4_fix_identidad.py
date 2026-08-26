"""G4/P1-1 — deriva de identidad: el choque con `ix_jobs_url` ya no es mudo.

Los providers `arbeitnow`/`jobgether` cambiaron en G3 la fórmula de su `hash`
(canonización de la URL) y la migración de datos NO se ejecutó. El upsert
declara el conflicto SOLO por `hash`, pero `jobs.url` tiene su propio índice
UNIQUE: cada oferta que el portal RE-LISTA en la misma url intenta un INSERT
con hash nuevo, muere por unicidad de `url`, el savepoint la aborta y el
`except Exception` por-oferta la contaba como un `errors` anónimo. La oferta se
descartaba, `last_seen_at` no se refrescaba y la fuente seguía saliendo `ok`
porque las URLs NUEVAS sí entraban: **pérdida silenciosa**.

El fix no repara la deriva (eso lo hace `scripts/g3_canonizacion_identidad_*`):
la hace VISIBLE — `JobIdentityConflictError` tipada, contador propio
`summary["identity_conflicts"]` e incidencia en `summary["unhealthy"]`.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from config import settings
from models.job import Job
from services.job_repository import JobRepository
from tasks.fetch_tasks import _fetch_providers_async
from tasks.scraping_tasks import _fetch_scrapers_async
from services.job_service import BaseJobProvider

_URL = "https://www.arbeitnow.com/jobs/companies/acme/senior-python-engineer-stuttgart"


def _job(job_hash: str, source: str, url: str = _URL) -> dict:
    """Oferta mínima válida, tal como la emite un provider tras normalizar."""
    return {
        "hash": job_hash,
        "source": source,
        "title": "Lehrperson Sekundarstufe",
        "company": "Acme",
        "url": url,
        # La ventana de cosecha descarta ALTAS sin fecha: la oferta re-listada
        # entra como alta (su hash nuevo no está en el corpus).
        "published_at": datetime.now(timezone.utc),
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


def _mock_session_factory(db_session):
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


def _make_provider(source_name: str, jobs: list[dict]):
    provider = MagicMock()
    provider.get_source_name.return_value = source_name
    provider.fetch_jobs = AsyncMock(return_value=jobs)
    return provider


def _make_scraper(source_name: str, jobs: list[dict]):
    scraper = MagicMock()
    scraper.get_source_name.return_value = source_name
    scraper.fetch_jobs = AsyncMock(return_value=jobs)
    scraper.job_identity = BaseJobProvider.job_identity
    scraper.PAGE_SIZE = 20
    scraper.MAX_PAGES = 5
    return scraper


class TestDerivaDeIdentidad:
    async def test_upsert_traduce_la_violacion_de_ix_jobs_url(self, db_session):
        """El repositorio distingue la colisión de IDENTIDAD de un fallo
        cualquiera de integridad: hash nuevo + url ya existente."""
        # Import local a propósito: sin el fix el módulo no exporta la clase y
        # un import de cabecera rompería la COLECCIÓN del fichero entero,
        # ocultando el fallo de comportamiento de los otros dos tests.
        from services.job_repository import JobIdentityConflictError

        repo = JobRepository(db_session)
        await repo.upsert_job(_job("a" * 32, "arbeitnow"))
        await db_session.commit()

        with pytest.raises(JobIdentityConflictError) as exc:
            async with db_session.begin_nested():
                await repo.upsert_job(_job("b" * 32, "arbeitnow"))
        await db_session.rollback()

        assert "DERIVA DE IDENTIDAD" in str(exc.value)
        assert "arbeitnow" in str(exc.value)
        assert "canonizacion" in str(exc.value) or "canonización" in str(exc.value)

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_la_cosecha_de_providers_no_traga_la_deriva(
        self, mock_providers, db_session
    ):
        """Camino real: la oferta re-listada con identidad nueva NO puede
        contarse como un `errors` anónimo — debe salir como incidencia."""
        repo = JobRepository(db_session)
        await repo.upsert_job(_job("a" * 32, "arbeitnow"))
        await db_session.commit()

        # El portal re-lista la MISMA url; el provider ya emite el hash nuevo.
        mock_providers.return_value = [
            _make_provider("arbeitnow", [_job("b" * 32, "arbeitnow")])
        ]
        with patch(
            "tasks.fetch_tasks.task_session", new=_mock_session_factory(db_session)
        ):
            summary = await _fetch_providers_async()

        assert summary.get("identity_conflicts") == 1, (
            "la deriva de identidad se está disolviendo entre los errores "
            "por-oferta: vuelve a ser pérdida silenciosa"
        )
        assert summary["errors"] == 0
        assert any("DERIVA DE IDENTIDAD" in m for m in summary["unhealthy"]), (
            "la fuente sale sana pese a estar descartando las re-listadas"
        )
        # La fila histórica sigue con su hash viejo: el fix NO migra datos.
        rows = list((await db_session.execute(select(Job.hash))).scalars())
        assert rows == ["a" * 32]

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_la_cosecha_de_scrapers_no_traga_la_deriva(
        self, mock_scrapers, monkeypatch, db_session
    ):
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", False)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", False)

        repo = JobRepository(db_session)
        await repo.upsert_job(_job("c" * 32, "scr_drift", "http://drift.test/a"))
        await db_session.commit()

        mock_scrapers.return_value = [
            _make_scraper(
                "scr_drift", [_job("d" * 32, "scr_drift", "http://drift.test/a")]
            )
        ]
        with patch(
            "tasks.scraping_tasks.task_session", new=_mock_session_factory(db_session)
        ):
            summary = await _fetch_scrapers_async()

        assert summary.get("identity_conflicts") == 1
        assert summary["errors"] == 0
        assert any("DERIVA DE IDENTIDAD" in m for m in summary["unhealthy"])
