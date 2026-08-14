"""Tests for the fetch_providers Celery task pipeline."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from tasks.fetch_tasks import _fetch_providers_async


def _make_mock_provider(source_name: str, jobs: list[dict]):
    """Create a mock provider that returns the given jobs."""
    provider = MagicMock()
    provider.get_source_name.return_value = source_name
    provider.fetch_jobs = AsyncMock(return_value=jobs)
    return provider


def _sample_job(title="Developer", company="Acme", url="http://a.com/1"):
    """Minimal valid job dict from a provider."""
    return {
        "hash": f"h_{title}_{company}_{url}"[:32].ljust(32, "0"),
        "source": "test",
        "title": title,
        "company": company,
        "url": url,
        "location": "Zurich",
        "canton": "ZH",
        "description": "Build software with Python and FastAPI for our team.",
        "description_snippet": "Build software...",
        "remote": False,
        "tags": ["python"],
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
    """Create a mock async_session factory that yields db_session."""

    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


class TestFetchPipeline:
    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_empty_providers_returns_zero(self, mock_providers, db_session):
        """No providers enabled -> summary shows zeros."""
        mock_providers.return_value = []

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_providers_async()

        assert summary["providers"] == 0
        assert summary["fetched"] == 0
        assert summary["new"] == 0

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_single_provider_stores_jobs(self, mock_providers, db_session):
        """A provider returning 2 jobs -> both stored as new."""
        jobs = [
            _sample_job("Dev A", "Acme", "http://a.com/1"),
            _sample_job("Dev B", "Beta", "http://b.com/2"),
        ]
        mock_providers.return_value = [_make_mock_provider("test_src", jobs)]

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_providers_async()

        assert summary["providers"] == 1
        assert summary["fetched"] == 2
        assert summary["new"] == 2

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_provider_failure_doesnt_stop_others(
        self, mock_providers, db_session
    ):
        """One provider fails -> the other still processes."""
        failing = MagicMock()
        failing.get_source_name.return_value = "failing_src"
        failing.fetch_jobs = AsyncMock(side_effect=RuntimeError("boom"))

        working = _make_mock_provider(
            "working_src",
            [_sample_job("Dev", "Acme", "http://w.com/1")],
        )
        mock_providers.return_value = [failing, working]

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_providers_async()

        assert summary["errors"] >= 1
        assert summary["providers"] >= 1

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_normalizer_applied(self, mock_providers, db_session):
        """Jobs pass through DataNormalizer (seniority inferred from title)."""
        jobs = [
            _sample_job(
                "Senior Python Developer",
                "Acme AG",
                "http://a.com/norm",
            )
        ]
        jobs[0]["description"] = (
            "We are looking for an experienced Python developer to join our "
            "engineering team in Zurich. You will build REST APIs using FastAPI "
            "and maintain our PostgreSQL databases."
        )
        mock_providers.return_value = [_make_mock_provider("test_src", jobs)]

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_providers_async()

        assert summary["new"] == 1

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_provider_que_no_guarda_nada_acaba_en_unhealthy(
        self, mock_providers, monkeypatch, db_session
    ):
        """VD.3: descargar N>0 y guardar 0 dos runs seguidos → la fuente
        aparece en unhealthy (antes quedaba `ok` mirando solo la descarga)."""
        from config import settings
        from services.job_repository import JobRepository

        monkeypatch.setattr(settings, "SOURCE_HEALTH_UNSTORED_STREAK", 2)

        # URL ocupada por OTRA oferta (otro hash): el INSERT colisiona contra
        # ix_jobs_url — el modo de fallo real de stelle_admin.
        repo = JobRepository(db_session)
        await repo.upsert_job(_sample_job("Occupant", "Other", "http://p.com/taken"))
        await db_session.commit()

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            mock_providers.return_value = [
                _make_mock_provider(
                    "unstored_src",
                    [_sample_job("Clash A", "Acme", "http://p.com/taken")],
                )
            ]
            summary1 = await _fetch_providers_async()
            assert summary1["unhealthy"] == []  # un run solo puede ser un hipo

            mock_providers.return_value = [
                _make_mock_provider(
                    "unstored_src",
                    [_sample_job("Clash B", "Acme", "http://p.com/taken")],
                )
            ]
            summary2 = await _fetch_providers_async()

        assert any(entry.startswith("unstored_src:") for entry in summary2["unhealthy"])

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_filtro_tech_no_degrada_la_fuente(
        self, mock_providers, monkeypatch, db_session
    ):
        """F1: las ofertas descartadas por el filtro tech nunca entran en el
        camino del savepoint — no pueden contar como "descargado sin guardar"
        ni degradar una fuente (y una BD) perfectamente sanas."""
        from sqlalchemy import select

        from config import settings
        from models.source_health import SourceHealth

        monkeypatch.setattr(settings, "SOURCE_HEALTH_UNSTORED_STREAK", 2)

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            # Dos runs con el lote ENTERO filtrado (perfil no técnico).
            for _ in range(2):
                mock_providers.return_value = [
                    _make_mock_provider(
                        "niche_src",
                        [
                            _sample_job(
                                "Pflegefachfrau 80%", "Spital", "http://n.com/1"
                            ),
                            _sample_job("DevOps Engineer", "Acme", "http://n.com/2"),
                        ],
                    )
                ]
                summary = await _fetch_providers_async()

        assert summary["unhealthy"] == []
        fila = (
            await db_session.execute(
                select(SourceHealth).where(SourceHealth.source_key == "niche_src")
            )
        ).scalar_one()
        assert fila.consecutive_unstored == 0

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_fallo_del_lote_cuenta_para_la_racha(
        self, mock_providers, monkeypatch, db_session
    ):
        """G6 (F3, camino de fetch_tasks): perder el LOTE entero (commit
        fallido tras los savepoints) tambien es señal de persistencia — sin
        el `record_storage` del except la racha quedaba congelada y el fallo
        se presentaba como exito un nivel mas arriba."""
        from sqlalchemy import select

        from config import settings
        from models.source_health import SourceHealth

        monkeypatch.setattr(settings, "SOURCE_HEALTH_UNSTORED_STREAK", 2)

        original_commit = db_session.commit

        def _fail_batch_commit():
            # Falla SOLO el commit del lote (el 2º del run): los commits de
            # source_health (el 1º de record_and_alert y el 3º de
            # record_storage) deben funcionar para que la racha se registre.
            state = {"n": 0}

            async def commit():
                state["n"] += 1
                if state["n"] == 2:
                    raise RuntimeError("BD caida en el commit del lote")
                await original_commit()

            return commit

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            for title in ("Batch A", "Batch B"):
                mock_providers.return_value = [
                    _make_mock_provider(
                        "fetch_lote",
                        [_sample_job(title, "Acme", "http://f.com/batch")],
                    )
                ]
                monkeypatch.setattr(db_session, "commit", _fail_batch_commit())
                summary = await _fetch_providers_async()

        assert summary["errors"] >= 1
        assert any(entry.startswith("fetch_lote:") for entry in summary["unhealthy"])
        fila = (
            await db_session.execute(
                select(SourceHealth).where(SourceHealth.source_key == "fetch_lote")
            )
        ).scalar_one()
        # Discriminacion: la racha refleja lotes INTENTADOS (attempted > 0) y
        # perdidos, y la señal de DESCARGA sigue intacta (la fuente descargo).
        assert fila.consecutive_unstored == 2
        assert fila.last_stored_count == 0
        assert fila.last_outcome == "ok"

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_duplicate_same_hash_counted_as_update(
        self, mock_providers, db_session
    ):
        """Re-fetching same job (same hash) -> counted as update, not new."""
        job = _sample_job("Dev", "Acme", "http://a.com/dup")
        mock_providers.return_value = [_make_mock_provider("src1", [job])]

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            # First fetch - new
            summary1 = await _fetch_providers_async()
            assert summary1["new"] == 1

            # Second fetch - update
            summary2 = await _fetch_providers_async()
            assert summary2["updated"] == 1
            assert summary2["new"] == 0
