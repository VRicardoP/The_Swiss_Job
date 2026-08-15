"""Tests del pipeline de scrapers (VD.2 + VD.3) — fallos que se presentaban
como éxitos.

Reproducen el modo de fallo REAL de `stelle_admin`: una oferta cuyo INSERT
colisiona contra `ix_jobs_url` (URL ya ocupada por otro hash). Antes de VD.2
el cursor aprendía igualmente su identidad (envenenado para siempre) y antes
de VD.3 la fuente seguía marcada `ok` en `source_health`.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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


class TestCursorAprendeDescartadasPorVentana:
    """K3 — excepción ACOTADA a VD.2: una descartada por FECHA fuera de
    ventana SÍ entra en el cursor (destino resuelto por política, determinista
    y monótono: su fecha no cambia y el corte solo avanza). Los fallos de
    persistencia y las sin-fecha siguen SIN entrar — VD.2 intacto para ellos.
    """

    @staticmethod
    def _windowed_job(title: str, url: str, published_at) -> dict:
        job = _sample_job(title, "School", url)
        job["source"] = "schuljobs"  # política WINDOW real
        job["published_at"] = published_at
        return job

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_descartada_por_fecha_si_entra_fallo_de_guardado_no(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """En un mismo run: la aceptada persistida entra; la descartada por
        fecha entra (K3 — sin esto se re-descarga y re-cuenta cada run y el
        early-stop nunca corta); la descartada SIN fecha no entra (su destino
        no está resuelto: un run posterior puede traer la fecha); la que
        FALLA al guardar no entra (VD.2 tal cual)."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", True)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", False)
        now = datetime.now(timezone.utc)

        await _occupy_url(db_session, "http://schuljobs.test/taken")
        jobs = [
            self._windowed_job(
                "Lehrperson OK", "http://schuljobs.test/ok", now - timedelta(days=1)
            ),
            self._windowed_job(
                "Lehrperson Stale",
                "http://schuljobs.test/stale",
                now - timedelta(days=30),
            ),
            self._windowed_job(
                "Lehrperson NoDate", "http://schuljobs.test/nodate", None
            ),
            # Mismo modo de fallo que stelle_admin: URL ocupada, hash distinto.
            self._windowed_job(
                "Lehrperson Fail",
                "http://schuljobs.test/taken",
                now - timedelta(days=1),
            ),
        ]
        mock_scrapers.return_value = [_make_mock_scraper("schuljobs", jobs)]

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_scrapers_async()

        assert summary["window_skipped"] == 1
        assert summary["window_no_date"] == 1
        assert summary["errors"] == 1
        cursor = (
            await db_session.execute(
                select(SourceCursor).where(SourceCursor.source_key == "schuljobs")
            )
        ).scalar_one()
        assert "http://schuljobs.test/ok" in cursor.recent_identities
        # K3: la descartada por fecha SÍ se aprende.
        assert "http://schuljobs.test/stale" in cursor.recent_identities
        # Sin fecha: destino NO resuelto — no se aprende.
        assert "http://schuljobs.test/nodate" not in cursor.recent_identities
        # VD.2 intacto: el fallo de persistencia no se aprende.
        assert "http://schuljobs.test/taken" not in cursor.recent_identities


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


class TestFalloAntesDelBucle:
    """L2 — un fallo en la pre-pasada de la ventana (known_hashes, conteo de
    corpus) pierde el lote entero ANTES de que el bucle arranque. Con
    `attempted_count = 0` la racha de persistencia no se movía y la fuente
    jamás se degradaba: el fallo-disfrazado-de-éxito de F1, reabierto."""

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_fallo_en_la_prepasada_mueve_la_racha(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """Dos runs con statement timeout recurrente en `known_hashes`
        (contención de locks sobre `jobs` durante el cleanup): el except
        registra la talla del lote descargado y la fuente acaba degradada."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", False)
        monkeypatch.setattr(settings, "SOURCE_HEALTH_UNSTORED_STREAK", 2)

        async def timing_out(self, hashes):
            raise RuntimeError("statement timeout en known_hashes")

        monkeypatch.setattr(JobRepository, "known_hashes", timing_out)
        now = datetime.now(timezone.utc)

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            for run in range(2):
                job = _sample_job(
                    f"Stale {run}", "School", f"http://schuljobs.test/pre{run}"
                )
                job["source"] = "schuljobs"  # política WINDOW real
                # Fuera de ventana: la pre-pasada SÍ consulta known_hashes.
                job["published_at"] = now - timedelta(days=30)
                mock_scrapers.return_value = [_make_mock_scraper("schuljobs", [job])]
                summary = await _fetch_scrapers_async()

        assert summary["errors"] >= 1
        assert any(entry.startswith("schuljobs:") for entry in summary["unhealthy"]), (
            f"la fuente no se degradó pese a perder el lote cada run: "
            f"{summary['unhealthy']}"
        )
        fila = (
            await db_session.execute(
                select(SourceHealth).where(SourceHealth.source_key == "schuljobs")
            )
        ).scalar_one()
        assert fila.consecutive_unstored == 2, (
            "la racha de persistencia no se movió con el fallo pre-bucle"
        )


class TestVentanaCosechaScrapers:
    """V.2/ADR-10 rev. J1 en el pipeline de scrapers: la excepción de los
    colegios, el filtro solo-altas y el anti-falso-positivo de la señal de
    persistencia."""

    @staticmethod
    def _scraper_job(source: str, title: str, url: str, published_at) -> dict:
        job = _sample_job(title, "School", url)
        job["source"] = source
        job["published_at"] = published_at
        return job

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_colegio_entra_entero_en_cualquier_run(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """Excepción de ADR-10: un swiss_schools_* (política FULL) se cosecha
        ENTERO aunque no traiga ni una fecha — también en un run posterior,
        con la fuente ya poblada (rev. J1: no hay noción de bootstrap)."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", False)

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            # Run 1 — fuente vacía.
            mock_scrapers.return_value = [
                _make_mock_scraper(
                    "swiss_schools_nae",
                    [
                        self._scraper_job(
                            "swiss_schools_nae",
                            "Teacher Primary",
                            "http://nae.test/1",
                            None,
                        ),
                        self._scraper_job(
                            "swiss_schools_nae",
                            "Teacher Music",
                            "http://nae.test/2",
                            None,
                        ),
                    ],
                )
            ]
            summary1 = await _fetch_scrapers_async()
            assert summary1["new"] == 2

            # Run 2 — la fuente ya tiene filas: sigue entrando todo.
            mock_scrapers.return_value = [
                _make_mock_scraper(
                    "swiss_schools_nae",
                    [
                        self._scraper_job(
                            "swiss_schools_nae",
                            "Teacher Arts",
                            "http://nae.test/3",
                            None,
                        ),
                    ],
                )
            ]
            summary2 = await _fetch_scrapers_async()

        assert summary2["new"] == 1
        assert summary2["window_skipped"] == 0
        assert summary2["window_no_date"] == 0

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_lote_fuera_de_ventana_no_degrada_la_fuente(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """El anti-falso-positivo (la trampa F1, camino de scrapers): un lote
        entero de ALTAS de una fuente WINDOW cae fuera de la ventana →
        descarte deliberado, NO racha de no-guardados ni fuente en unhealthy.
        En ningún run (rev. J1)."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", False)
        monkeypatch.setattr(settings, "SOURCE_HEALTH_UNSTORED_STREAK", 2)
        now = datetime.now(timezone.utc)

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            # Dos runs de `schuljobs` (política WINDOW real): nada se guarda
            # y el lote entero (siempre altas) cae fuera en ambos.
            for run in range(2):
                mock_scrapers.return_value = [
                    _make_mock_scraper(
                        "schuljobs",
                        [
                            self._scraper_job(
                                "schuljobs",
                                f"Lehrperson {run}",
                                f"http://schuljobs.test/stale{run}",
                                now - timedelta(days=60),
                            )
                        ],
                    )
                ]
                summary = await _fetch_scrapers_async()

        assert summary["window_skipped"] == 1
        assert summary["unhealthy"] == []
        fila = (
            await db_session.execute(
                select(SourceHealth).where(SourceHealth.source_key == "schuljobs")
            )
        ).scalar_one()
        assert fila.consecutive_unstored == 0
        assert fila.last_stored_count == 0

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_revista_fuera_de_ventana_se_sigue_refrescando(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """Punto 2 de J1 en el camino de scrapers: una oferta vieja que YA
        está en `jobs` pasa por el upsert (updated, no descartada) y su
        last_seen_at avanza."""
        from models.job import Job

        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", False)
        now = datetime.now(timezone.utc)
        vieja = self._scraper_job(
            "schuljobs",
            "Lehrperson Vintage",
            "http://schuljobs.test/vintage",
            now - timedelta(days=30),
        )
        repo = JobRepository(db_session)
        await repo.upsert_job(dict(vieja))
        await db_session.commit()
        before = (
            await db_session.execute(
                select(Job.last_seen_at).where(Job.hash == vieja["hash"])
            )
        ).scalar_one()

        mock_scrapers.return_value = [_make_mock_scraper("schuljobs", [dict(vieja)])]
        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_scrapers_async()

        assert summary["updated"] == 1
        assert summary["window_skipped"] == 0
        db_session.expire_all()
        after = (
            await db_session.execute(
                select(Job.last_seen_at).where(Job.hash == vieja["hash"])
            )
        ).scalar_one()
        assert after > before, "la re-vista fuera de ventana no se refrescó"


class TestScraperQueLanzaDejaSenalDeSalud:
    """VD.10 (H5) — asimetría con providers: si `fetch_jobs` de un scraper
    LANZA, el flujo normal nunca llega a `record_and_alert` y el run no dejaba
    NINGUNA señal de descarga en source_health — un scraper petando en cada
    run era invisible. El camino análogo de `fetch_tasks` sí sintetiza el
    OUTCOME_ERROR."""

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_fetch_que_lanza_registra_error_de_descarga(
        self, mock_scrapers, monkeypatch, db_session
    ):
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", False)

        scraper = _make_mock_scraper("scr_boom", [])
        scraper.fetch_jobs = AsyncMock(side_effect=RuntimeError("TLS handshake roto"))
        mock_scrapers.return_value = [scraper]

        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_scrapers_async()

        assert summary["errors"] == 1
        assert summary["fetch_failed"] == 1
        fila = (
            await db_session.execute(
                select(SourceHealth).where(SourceHealth.source_key == "scr_boom")
            )
        ).scalar_one()
        assert fila.last_outcome == "error"
        assert fila.consecutive_errors == 1
        assert "RuntimeError" in (fila.last_error_detail or "")
        # Sin descarga no hay señal de persistencia (no se inventa racha).
        assert fila.consecutive_unstored == 0
