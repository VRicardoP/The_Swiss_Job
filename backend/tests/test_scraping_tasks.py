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
from services.crawler_budget import CrawlerBudgetService
from services.job_repository import JobRepository
from services.job_service import BaseJobProvider
from tasks.scraping_tasks import _fetch_scrapers_async
from utils import fetch_diagnostics as diag


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


# --- B-4 / A2-2 — el presupuesto no puede autolimitarse y el cursor no
# --- aprende de runs fallidos -------------------------------------------------


class _PagingScraper(BaseJobProvider):
    """Scraper sintético con el contrato REAL de paginación de los scrapers:
    respeta `_pages_budget()`, corta con `_page_all_known` (early-stop) y deja
    `_stop_reason` — igual que scraper_engine/irishjobs/schuljobs. Reproduce
    la cosecha newest-first de un portal; con PAGE_SIZE=1 es el caso agudo de
    `tes` (el servidor impone 1 oferta por página)."""

    SOURCE_NAME = "scr_paging"
    PAGE_SIZE = 1
    MAX_PAGES = 35

    def __init__(self, listing: list[dict]):
        super().__init__()
        # Listado del portal, lo más nuevo primero (como un portal real).
        self._listing = listing
        self.pages_fetched = 0

    async def fetch_jobs(self, query, location="Switzerland"):
        results: list[dict] = []
        for page in range(self._pages_budget()):
            start = page * self.PAGE_SIZE
            page_jobs = self._listing[start : start + self.PAGE_SIZE]
            if not page_jobs:
                break  # fin del listado
            self.pages_fetched += 1
            if self._page_all_known(page_jobs):
                self._stop_reason = "known_page"
                break
            results.extend(page_jobs)
        return results

    def normalize_job(self, raw):
        return raw


async def _seed_cursor(
    db_session,
    source: str,
    identities: list[str],
    avg_new: float = 0.3,
    **overrides,
) -> SourceCursor:
    """Cursor estacionario ya bootstrapeado, como el de una fuente en régimen."""
    cursor = SourceCursor(
        source_key=source,
        scope_key="default",
        recent_identities=identities,
    )
    cursor.bootstrap_complete = True
    cursor.avg_new_jobs_per_run = avg_new
    cursor.avg_pages_per_run = 1.0
    cursor.consecutive_empty_runs = 0
    for key, value in overrides.items():
        setattr(cursor, key, value)
    db_session.add(cursor)
    await db_session.commit()
    return cursor


class TestPresupuestoNoSeAutolimita:
    """B-4 — lazo de autolimitación: `avg_new` es una EMA de `new_count` y
    `new_count` nunca puede superar `presupuesto × page_size`, así que la EMA
    jamás aprende una demanda mayor que el techo que ella misma fija. Con
    cosecha newest-first, lo que queda bajo el horizonte del presupuesto se
    hunde y no se recupera nunca (la auditoría midió: ráfaga de 5 con
    page_size=1 → [n1, n2] cosechadas, [n3, n4, n5] perdidas para siempre)."""

    OLD_URL = "http://paging.test/old"
    BURST_URLS = [f"http://paging.test/n{i}" for i in range(1, 6)]

    def _listing(self) -> list[dict]:
        """Listado fresco por run: el pipeline muta los dicts al normalizar."""
        jobs = [
            _sample_job(f"Burst {url.rsplit('/', 1)[-1]}", "Acme", url)
            for url in self.BURST_URLS
        ]
        jobs.append(_sample_job("Old Offer", "Acme", self.OLD_URL))
        for job in jobs:
            job["source"] = "scr_paging"
        return jobs

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_rafaga_sobre_el_presupuesto_acaba_cosechada(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """Ráfaga de 5 novedades con presupuesto estacionario de 2 páginas
        (page_size=1): el run 1 corta por presupuesto SIN early-stop (run "con
        hambre") → re-abre el bootstrap → el run 2 recibe la ventana completa
        sin cursor inyectado y re-sincroniza. Las 5 acaban cosechadas y la
        fuente vuelve a régimen (sin bucle de re-syncs)."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", True)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", True)
        # Fija el margen para que el presupuesto estacionario sea 2 páginas
        # (el escenario medido por la auditoría), pase lo que pase en config.
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_SAFETY_PAGES", 1)
        await _seed_cursor(db_session, "scr_paging", [self.OLD_URL])

        pages_per_run: list[int] = []
        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            for _ in range(3):
                scraper = _PagingScraper(self._listing())
                mock_scrapers.return_value = [scraper]
                await _fetch_scrapers_async()
                pages_per_run.append(scraper.pages_fetched)

        cursor = (
            await db_session.execute(
                select(SourceCursor).where(SourceCursor.source_key == "scr_paging")
            )
        ).scalar_one()
        perdidas = [u for u in self.BURST_URLS if u not in cursor.recent_identities]
        assert perdidas == [], f"ofertas de la ráfaga NUNCA cosechadas: {perdidas}"
        # Re-sincronizada: no se queda en bucle de bootstraps.
        assert cursor.bootstrap_complete is True
        # Coste acotado: 2 (hambre) + 6 (re-sync, el listado entero) + 1
        # (tranquila, early-stop en página 1) — nada de 35 páginas por run.
        assert pages_per_run == [2, 6, 1]

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_fuente_tranquila_no_dispara_paginas_extra(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """Control: una fuente sin novedades hace early-stop en la página 1,
        NO se marca "con hambre" y su presupuesto sigue mínimo."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", True)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", True)
        await _seed_cursor(db_session, "scr_paging", [self.OLD_URL])

        job = _sample_job("Old Offer", "Acme", self.OLD_URL)
        job["source"] = "scr_paging"
        scraper = _PagingScraper([job])
        mock_scrapers.return_value = [scraper]
        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            await _fetch_scrapers_async()

        assert scraper.pages_fetched == 1
        assert scraper._stop_reason == "known_page"
        cursor = (
            await db_session.execute(
                select(SourceCursor).where(SourceCursor.source_key == "scr_paging")
            )
        ).scalar_one()
        # Sin hambre: el bootstrap sigue completo y el próximo run seguirá
        # presupuestado al mínimo (proporcional a novedades, doctrina capa 3).
        assert cursor.bootstrap_complete is True
        assert (
            CrawlerBudgetService.max_pages_this_run(
                cursor, _PagingScraper.PAGE_SIZE, _PagingScraper.MAX_PAGES
            )
            <= 1 + settings.CRAWLER_BUDGET_SAFETY_PAGES
        )


class TestCursorNoAprendeDeRunsFallidos:
    """A2-2 — `update_after_run` se ejecutaba también con OUTCOME_ERROR
    (404/timeout/soft-block devuelven [] sin excepción): marcaba
    `bootstrap_complete=True` con `avg_new=0` (bootstrap perdido para
    siempre, presupuestada a 2 páginas), engordaba `consecutive_empty_runs`
    (backoff de sequía sobre una fuente ROTA) y `last_success_at` mentía.
    Caso real en BD: gastrojob y myscience."""

    @staticmethod
    def _failing_scraper(source: str):
        """Scraper cuyo run falla como en producción: registra el fallo de
        descarga en fetch_diagnostics y devuelve [] sin lanzar."""
        scraper = _make_mock_scraper(source, [])

        async def _failing_fetch(query, location="Switzerland"):
            diag.record(diag.KIND_HTTP, url=f"http://{source}.test/list", status=404)
            return []

        scraper.fetch_jobs = _failing_fetch
        return scraper

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_primer_run_con_error_no_completa_bootstrap(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """Una fuente NUEVA cuyo primer run falla NO queda bootstrapeada: el
        próximo run debe recibir la ventana completa, no 2 páginas."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", True)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", True)

        mock_scrapers.return_value = [self._failing_scraper("scr_err")]
        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_scrapers_async()

        assert summary["fetch_failed"] == 1
        cursor = (
            await db_session.execute(
                select(SourceCursor).where(SourceCursor.source_key == "scr_err")
            )
        ).scalar_one()
        assert cursor.bootstrap_complete is False
        assert cursor.last_success_at is None
        assert (cursor.consecutive_empty_runs or 0) == 0
        # El bootstrap con ventana completa sigue pendiente para cuando la
        # fuente responda.
        assert CrawlerBudgetService.max_pages_this_run(cursor, 1, 35) == 35

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_run_con_error_no_toca_el_cursor_sano(
        self, mock_scrapers, monkeypatch, db_session
    ):
        """Un run fallido sobre una fuente en régimen no engorda la racha de
        vacíos (a 3 entraría en backoff de sequía por un fallo), no decae la
        EMA y no miente en last_success_at."""
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", True)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", True)
        t0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        await _seed_cursor(
            db_session,
            "scr_err2",
            ["http://scr_err2.test/known"],
            avg_new=4.0,
            consecutive_empty_runs=2,
            last_success_at=t0,
            last_run_at=t0,
        )

        mock_scrapers.return_value = [self._failing_scraper("scr_err2")]
        with patch(
            "tasks.scraping_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_scrapers_async()

        assert summary["fetch_failed"] == 1
        db_session.expire_all()
        cursor = (
            await db_session.execute(
                select(SourceCursor).where(SourceCursor.source_key == "scr_err2")
            )
        ).scalar_one()
        assert cursor.consecutive_empty_runs == 2, (
            "el run FALLIDO engordó la racha de vacíos (backoff de sequía "
            "sobre una fuente rota)"
        )
        assert cursor.last_success_at == t0, "last_success_at miente"
        assert cursor.avg_new_jobs_per_run == 4.0, "la EMA decayó por un fallo"
        assert cursor.recent_identities == ["http://scr_err2.test/known"]
