"""Tests for the fetch_providers Celery task pipeline."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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
    async def test_vd8_revista_tech_guardada_refresca_y_alta_tech_sigue_fuera(
        self, mock_providers, db_session
    ):
        """VD.8 — el filtro tech, como la ventana (J1), solo puede rechazar
        ALTAS, nunca re-vistas: una oferta YA guardada cuyo título casa con
        una keyword tech sigue pasando por el upsert y su last_seen_at
        avanza — si el filtro la saltara, dejaría de refrescarse y
        cleanup_stale_jobs la archivaría a los 60 días aunque siga viva en
        el portal. Y una tech NO guardada sigue sin entrar: qué altas
        ingresan lo decide el filtro, exactamente como antes."""
        from sqlalchemy import select

        from models.job import Job
        from services.job_repository import JobRepository

        # Oferta ya en corpus cuyo título casa con una keyword tech (p. ej.
        # entró antes de añadirse la keyword, o su título cambió).
        guardada = _sample_job("DevOps Engineer", "Acme", "http://t.com/kept")
        guardada["source"] = "niche_src"
        repo = JobRepository(db_session)
        await repo.upsert_job(dict(guardada))
        await db_session.commit()
        before = (
            await db_session.execute(
                select(Job.last_seen_at).where(Job.hash == guardada["hash"])
            )
        ).scalar_one()

        # El portal re-muestra la guardada y trae además un ALTA tech nueva.
        alta_tech = _sample_job("Backend Developer", "Acme", "http://t.com/new")
        alta_tech["source"] = "niche_src"
        mock_providers.return_value = [
            _make_mock_provider("niche_src", [dict(guardada), alta_tech])
        ]
        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_providers_async()

        # La re-vista pasó por el upsert y se refrescó...
        assert summary["updated"] == 1
        db_session.expire_all()
        after = (
            await db_session.execute(
                select(Job.last_seen_at).where(Job.hash == guardada["hash"])
            )
        ).scalar_one()
        assert after > before, "la re-vista tech guardada no refrescó last_seen_at"
        # ...y el alta tech sigue descartada por el filtro: no entró en BD.
        assert summary["new"] == 0
        urls = (
            (await db_session.execute(select(Job.url).where(Job.source == "niche_src")))
            .scalars()
            .all()
        )
        assert urls == [guardada["url"]]

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


class TestVentanaCosecha:
    """V.2/ADR-10 rev. J1 — la ventana aplica en TODOS los runs y solo a
    ALTAS: deja rastro contable, nunca descarta re-vistas y nunca cuenta sus
    descartes como fallos de persistencia."""

    @staticmethod
    def _dated_job(title: str, url: str, published_at) -> dict:
        """Job de la fuente `ostjob` (política WINDOW real) con fecha dada."""
        job = _sample_job(title, "Acme", url)
        job["source"] = "ostjob"
        job["published_at"] = published_at
        return job

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_j1_la_ventana_sigue_filtrando_altas_con_la_fuente_ya_poblada(
        self, mock_providers, db_session
    ):
        """El test que fija J1 y que impide que la ventana vuelva a ser
        decorativa. Run 1 (fuente vacía): del lote mixto solo entra la
        reciente. Run 2 (la fuente YA tiene filas — el escenario en el que la
        versión "solo en bootstrap" re-ingería todo): las viejas SIGUEN sin
        guardarse, y la reciente re-vista se refresca con normalidad."""
        from sqlalchemy import select

        from models.job import Job

        now = datetime.now(timezone.utc)

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            # Run 1 — lote mixto sobre fuente sin filas.
            mock_providers.return_value = [
                _make_mock_provider(
                    "ostjob",
                    [
                        self._dated_job(
                            "Editor A", "http://o.com/new", now - timedelta(days=1)
                        ),
                        self._dated_job(
                            "Editor B", "http://o.com/old", now - timedelta(days=30)
                        ),
                    ],
                )
            ]
            summary1 = await _fetch_providers_async()
            assert summary1["new"] == 1
            assert summary1["window_skipped"] == 1

            # Run 2 — la fuente ya tiene corpus; el portal re-muestra su
            # listado entero (los providers no tienen cursor ni early-stop).
            mock_providers.return_value = [
                _make_mock_provider(
                    "ostjob",
                    [
                        self._dated_job(
                            "Editor A", "http://o.com/new", now - timedelta(days=1)
                        ),
                        self._dated_job(
                            "Editor B", "http://o.com/old", now - timedelta(days=30)
                        ),
                        self._dated_job(
                            "Editor C", "http://o.com/old2", now - timedelta(days=60)
                        ),
                    ],
                )
            ]
            summary2 = await _fetch_providers_async()

        # Las altas viejas siguen fuera; la re-vista reciente se refresca.
        assert summary2["new"] == 0
        assert summary2["updated"] == 1
        assert summary2["window_skipped"] == 2
        urls = (
            (await db_session.execute(select(Job.url).where(Job.source == "ostjob")))
            .scalars()
            .all()
        )
        assert urls == ["http://o.com/new"]

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_revista_fuera_de_ventana_refresca_last_seen_at(
        self, mock_providers, db_session
    ):
        """La regresión que el punto 2 de J1 impide: una oferta vieja QUE YA
        ESTÁ en `jobs` sigue pasando por el upsert aunque caiga fuera de la
        ventana — su last_seen_at avanza. Si el filtro la saltara, dejaría de
        refrescarse y cleanup_stale_jobs la archivaría por "desaparecida"."""
        from sqlalchemy import select

        from models.job import Job
        from services.job_repository import JobRepository

        now = datetime.now(timezone.utc)
        vieja = self._dated_job(
            "Editor Vintage", "http://o.com/vintage", now - timedelta(days=30)
        )
        repo = JobRepository(db_session)
        await repo.upsert_job(dict(vieja))
        await db_session.commit()
        before = (
            await db_session.execute(
                select(Job.last_seen_at).where(Job.hash == vieja["hash"])
            )
        ).scalar_one()

        # El portal la re-muestra, con su misma fecha fuera de ventana.
        mock_providers.return_value = [_make_mock_provider("ostjob", [dict(vieja)])]
        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_providers_async()

        assert summary["updated"] == 1
        assert summary["window_skipped"] == 0
        db_session.expire_all()
        after = (
            await db_session.execute(
                select(Job.last_seen_at).where(Job.hash == vieja["hash"])
            )
        ).scalar_one()
        assert after > before, "la re-vista fuera de ventana no se refrescó"

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_una_sola_consulta_por_fuente_y_solo_hashes_fuera(
        self, mock_providers, monkeypatch, db_session
    ):
        """K5 — el coste queda acotado por LOTE, no por oferta: una única
        llamada a `known_hashes` por fuente, y solo con los hashes de las
        ofertas fuera de ventana (las de dentro no pagan consulta)."""
        from services.job_repository import JobRepository

        calls: list[set[str]] = []
        original = JobRepository.known_hashes

        async def counting(self, hashes):
            calls.append(set(hashes))
            return await original(self, hashes)

        monkeypatch.setattr(JobRepository, "known_hashes", counting)

        now = datetime.now(timezone.utc)
        dentro_a = self._dated_job(
            "Editor In A", "http://o.com/in-a", now - timedelta(days=1)
        )
        dentro_b = self._dated_job(
            "Editor In B", "http://o.com/in-b", now - timedelta(days=2)
        )
        fuera_a = self._dated_job(
            "Editor Out A", "http://o.com/out-a", now - timedelta(days=30)
        )
        fuera_b = self._dated_job(
            "Editor Out B", "http://o.com/out-b", now - timedelta(days=45)
        )
        mock_providers.return_value = [
            _make_mock_provider("ostjob", [dentro_a, fuera_a, dentro_b, fuera_b])
        ]

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_providers_async()

        assert summary["new"] == 2
        assert summary["window_skipped"] == 2
        # Dos fuera de ventana ⇒ UNA llamada con AMBOS hashes (por-oferta
        # serían dos llamadas de un hash cada una).
        assert calls == [{fuera_a["hash"], fuera_b["hash"]}], (
            "known_hashes debe llamarse UNA vez por fuente y solo con los "
            "hashes fuera de ventana"
        )

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_lote_fuera_de_ventana_no_degrada_la_fuente(
        self, mock_providers, monkeypatch, db_session
    ):
        """El anti-falso-positivo (la trampa que ya mordió como F1): un lote
        ENTERO de altas fuera de la ventana es un descarte deliberado, no un
        fallo de persistencia — la fuente NO aparece en unhealthy ni
        incrementa consecutive_unstored. En NINGÚN run (rev. J1)."""
        from sqlalchemy import select

        from config import settings
        from models.source_health import SourceHealth

        monkeypatch.setattr(settings, "SOURCE_HEALTH_UNSTORED_STREAK", 2)
        now = datetime.now(timezone.utc)

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            # Dos runs: nada se guarda y el lote entero (siempre altas) cae
            # fuera de la ventana en ambos.
            for run in range(2):
                mock_providers.return_value = [
                    _make_mock_provider(
                        "ostjob",
                        [
                            self._dated_job(
                                f"Editor {run}-{i}",
                                f"http://o.com/stale{run}{i}",
                                now - timedelta(days=60),
                            )
                            for i in range(2)
                        ],
                    )
                ]
                summary = await _fetch_providers_async()

        assert summary["window_skipped"] == 2
        assert summary["unhealthy"] == []
        fila = (
            await db_session.execute(
                select(SourceHealth).where(SourceHealth.source_key == "ostjob")
            )
        ).scalar_one()
        assert fila.consecutive_unstored == 0

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_window_sin_fecha_cuenta_aparte_y_dispara_guardarrail(
        self, mock_providers, monkeypatch, caplog, db_session
    ):
        """Política WINDOW sin published_at: las altas cuentan en
        window_no_date (no en window_skipped) y, si NINGUNA oferta del lote
        traía fecha, ERROR de política mal asignada — en cualquier run.
        (L4: el guardarraíl exige tamaño mínimo de lote; se baja el umbral
        para testear el cableado con un lote corto.)"""
        from config import settings

        monkeypatch.setattr(settings, "HARVEST_ALERT_MIN_BATCH", 2)
        jobs = [
            self._dated_job("Editor N1", "http://o.com/n1", None),
            self._dated_job("Editor N2", "http://o.com/n2", None),
        ]
        mock_providers.return_value = [_make_mock_provider("ostjob", jobs)]

        with (
            patch(
                "tasks.fetch_tasks.task_session",
                new=_mock_session_factory(db_session),
            ),
            caplog.at_level(logging.ERROR, logger="services.harvest_window"),
        ):
            summary = await _fetch_providers_async()

        assert summary["window_no_date"] == 2
        assert summary["window_skipped"] == 0
        assert summary["new"] == 0
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any(
            "ostjob" in r.getMessage() and "mal asignada" in r.getMessage()
            for r in errores
        ), "faltó el ERROR del guardarraíl de política mal asignada"

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_fallo_parcial_de_fechas_warning_desde_el_pipeline(
        self, mock_providers, monkeypatch, caplog, db_session
    ):
        """K2 en el pipeline real (verifica el cableado de `new_count`): las
        re-vistas traen fecha (el ERROR total calla) pero TODAS las altas del
        run caen por falta de fecha ⇒ WARNING desde el primer run. (L4: el
        WARNING exige >= 3 descartes sin fecha y tamaño mínimo de lote.)"""
        from config import settings
        from services.job_repository import JobRepository

        monkeypatch.setattr(settings, "HARVEST_ALERT_MIN_BATCH", 4)
        now = datetime.now(timezone.utc)
        revista = self._dated_job(
            "Editor Revisited", "http://o.com/revisited", now - timedelta(days=2)
        )
        repo = JobRepository(db_session)
        await repo.upsert_job(dict(revista))
        await db_session.commit()

        jobs = [
            dict(revista),  # re-vista con fecha: el guardarraíl total calla
            self._dated_job("Editor ND1", "http://o.com/nd1", None),
            self._dated_job("Editor ND2", "http://o.com/nd2", None),
            self._dated_job("Editor ND3", "http://o.com/nd3", None),
        ]
        mock_providers.return_value = [_make_mock_provider("ostjob", jobs)]

        with (
            patch(
                "tasks.fetch_tasks.task_session",
                new=_mock_session_factory(db_session),
            ),
            caplog.at_level(logging.INFO, logger="services.harvest_window"),
        ):
            summary = await _fetch_providers_async()

        assert summary["window_no_date"] == 3
        assert summary["updated"] == 1
        assert summary["new"] == 0
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "ostjob" in r.getMessage() and "falta de fecha" in r.getMessage()
            for r in warnings
        ), "faltó el WARNING del fallo parcial de fechas (K2)"
        assert not [r for r in caplog.records if r.levelno == logging.ERROR]

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_deriva_de_identidad_error_y_unhealthy(
        self, mock_providers, monkeypatch, caplog, db_session
    ):
        """K1/L1, el caso que SIGUE gritando tras la ronda 2: corpus grande
        (comparable al lote), lote grande, nada reconocido por su mismo hash
        y CON descartes por fecha (p. ej. ostjob cambió su esquema de URLs).
        Ninguna re-vista se reconoce ⇒ las viejas se descartan como altas,
        dejan de refrescar last_seen_at y a los 60 días el cleanup las
        BORRARÍA. Tiene que gritar: ERROR + entrada en unhealthy."""
        from config import settings
        from services.job_repository import JobRepository

        monkeypatch.setattr(settings, "HARVEST_ALERT_MIN_BATCH", 10)
        now = datetime.now(timezone.utc)

        # Corpus previo de la fuente (URLs con el esquema viejo), COMPARABLE
        # al lote (cláusula 3 de watch_drift: corpus >= len(lote)).
        repo = JobRepository(db_session)
        for i in range(12):
            await repo.upsert_job(
                self._dated_job(
                    f"Editor Corpus {i}",
                    f"http://o.com/old-scheme/{i}",
                    now - timedelta(days=3),
                )
            )
        await db_session.commit()

        # Run con esquema NUEVO de URLs: hashes distintos en todo el lote, y
        # la mayoría del listado de un portal vivo está fuera de la ventana.
        drifted = [
            self._dated_job(
                f"Drifted {i}",
                f"http://o.com/new-scheme/{i}",
                now - timedelta(days=30),
            )
            for i in range(10)
        ]
        mock_providers.return_value = [_make_mock_provider("ostjob", drifted)]

        with (
            patch(
                "tasks.fetch_tasks.task_session",
                new=_mock_session_factory(db_session),
            ),
            caplog.at_level(logging.ERROR, logger="services.harvest_window"),
        ):
            summary = await _fetch_providers_async()

        assert any(
            entry.startswith("ostjob:") and "deriva de identidad" in entry
            for entry in summary["unhealthy"]
        ), f"faltó la entrada de deriva en unhealthy: {summary['unhealthy']}"
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any(
            "ostjob" in r.getMessage() and "deriva de identidad" in r.getMessage()
            for r in errores
        ), "faltó el ERROR de deriva de identidad"

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_deriva_con_duplicado_fuzzy_presente_sigue_gritando(
        self, mock_providers, monkeypatch, caplog, db_session
    ):
        """L1, falso NEGATIVO cerrado: deriva real con UN duplicado fuzzy en
        el lote. `find_fuzzy_duplicate` solo casa con OTRA fuente
        (`Job.source != source`): un dupe cross-source no dice nada de la
        estabilidad de identidad de ESTA fuente — lo habitual entre boards
        remotos que sindican contenido. Antes el dupe contaba como
        "reconocida" y el detector callaba mientras el corpus moría a los 60
        días; ahora el ERROR salta igual."""
        from config import settings
        from services.deduplicator import Deduplicator
        from services.job_repository import JobRepository

        monkeypatch.setattr(settings, "HARVEST_ALERT_MIN_BATCH", 10)
        now = datetime.now(timezone.utc)
        repo = JobRepository(db_session)

        # Corpus de ostjob comparable al lote (12 >= 12), esquema viejo.
        for i in range(12):
            await repo.upsert_job(
                self._dated_job(
                    f"Editor Corpus {i}",
                    f"http://o.com/old-scheme/{i}",
                    now - timedelta(days=3),
                )
            )
        # Oferta de OTRA fuente que casará por fuzzy con una alta del lote.
        syndicated = _sample_job("Drifted In", "Acme", "http://remotive.test/x")
        syndicated["source"] = "remotive"
        syndicated["fuzzy_hash"] = Deduplicator.compute_fuzzy_hash("Drifted In", "Acme")
        await repo.upsert_job(syndicated)
        await db_session.commit()

        # Lote con esquema NUEVO de URLs: 11 fuera de ventana (descartes por
        # fecha) + 1 dentro que resulta duplicado fuzzy de remotive.
        drifted = [
            self._dated_job(
                f"Drifted {i}",
                f"http://o.com/new-scheme/{i}",
                now - timedelta(days=30),
            )
            for i in range(11)
        ]
        drifted.append(
            self._dated_job(
                "Drifted In", "http://o.com/new-scheme/in", now - timedelta(days=1)
            )
        )
        mock_providers.return_value = [_make_mock_provider("ostjob", drifted)]

        with (
            patch(
                "tasks.fetch_tasks.task_session",
                new=_mock_session_factory(db_session),
            ),
            caplog.at_level(logging.ERROR, logger="services.harvest_window"),
        ):
            summary = await _fetch_providers_async()

        # El dupe cross-source ocurrió de verdad (si no, el test no prueba nada).
        assert summary["dupes"] == 1
        assert any(
            entry.startswith("ostjob:") and "deriva de identidad" in entry
            for entry in summary["unhealthy"]
        ), f"el dupe fuzzy silenció la deriva: {summary['unhealthy']}"
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any(
            "ostjob" in r.getMessage() and "deriva de identidad" in r.getMessage()
            for r in errores
        ), "faltó el ERROR de deriva con dupe fuzzy presente"

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_lote_entero_en_ventana_y_todo_nuevo_en_silencio(
        self, mock_providers, monkeypatch, caplog, db_session
    ):
        """L1, falso positivo nº 1 cerrado: lote ENTERO dentro de ventana y
        todo altas nuevas (tes/financejobs con presupuesto de 1 página tras
        un periodo tranquilo). Sin descartes por antigüedad no hay pérdida
        posible — la deriva con todo dentro de ventana solo produciría
        duplicados, recuperables. Silencio, aunque no se reconozca nada."""
        from config import settings
        from services.job_repository import JobRepository

        monkeypatch.setattr(settings, "HARVEST_ALERT_MIN_BATCH", 10)
        now = datetime.now(timezone.utc)
        repo = JobRepository(db_session)

        # Corpus grande: la cláusula del conteo NO es la que silencia aquí.
        for i in range(12):
            await repo.upsert_job(
                self._dated_job(
                    f"Editor Corpus {i}",
                    f"http://o.com/corpus/{i}",
                    now - timedelta(days=3),
                )
            )
        await db_session.commit()

        frescas = [
            self._dated_job(
                f"Fresh Page {i}", f"http://o.com/fresh/{i}", now - timedelta(days=1)
            )
            for i in range(10)
        ]
        mock_providers.return_value = [_make_mock_provider("ostjob", frescas)]

        with (
            patch(
                "tasks.fetch_tasks.task_session",
                new=_mock_session_factory(db_session),
            ),
            caplog.at_level(logging.ERROR, logger="services.harvest_window"),
        ):
            summary = await _fetch_providers_async()

        assert summary["new"] == 10
        assert summary["unhealthy"] == []
        assert not [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "deriva" in r.getMessage()
        ], "fuente sana con página de novedades recientes disparó deriva"

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_corpus_pequeno_retirado_del_listado_en_silencio(
        self, mock_providers, monkeypatch, caplog, db_session
    ):
        """L1, falso positivo nº 2 cerrado: fuente WINDOW de bajo volumen
        (zebis: 2 filas en corpus) cuyo corpus el portal retiró del listado —
        el lote son >= 10 ofertas viejas, ninguna en `jobs`. Con 2 filas y un
        lote de 10, no reconocer nada no es anómalo: sin esta cláusula el
        ERROR de deriva (con su diagnóstico de revisar normalize_job/
        compute_hash) saltaba EN CADA RUN y fatigaba el canal."""
        from config import settings
        from services.job_repository import JobRepository

        monkeypatch.setattr(settings, "HARVEST_ALERT_MIN_BATCH", 10)
        now = datetime.now(timezone.utc)
        repo = JobRepository(db_session)

        # Corpus minúsculo (2 filas), ya no presente en el listado.
        for i in range(2):
            await repo.upsert_job(
                self._dated_job(
                    f"Editor Small {i}",
                    f"http://o.com/small/{i}",
                    now - timedelta(days=3),
                )
            )
        await db_session.commit()

        listado_viejo = [
            self._dated_job(
                f"Old Listing {i}",
                f"http://o.com/listing/{i}",
                now - timedelta(days=30),
            )
            for i in range(10)
        ]
        mock_providers.return_value = [_make_mock_provider("ostjob", listado_viejo)]

        with (
            patch(
                "tasks.fetch_tasks.task_session",
                new=_mock_session_factory(db_session),
            ),
            caplog.at_level(logging.ERROR, logger="services.harvest_window"),
        ):
            summary = await _fetch_providers_async()

        assert summary["window_skipped"] == 10
        assert summary["unhealthy"] == []
        assert not [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "deriva" in r.getMessage()
        ], "corpus de 2 filas con lote de 10 disparó el falso positivo de deriva"

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_fallo_antes_del_bucle_mueve_la_racha_de_persistencia(
        self, mock_providers, monkeypatch, db_session
    ):
        """L2 — la pre-pasada mete consultas a BD ANTES del bucle
        (known_hashes, conteo de corpus): si fallan de forma recurrente, el
        lote se pierde entero en cada run. Con `attempted_count = 0` la racha
        no se movía y la fuente jamás se degradaba — el fallo-disfrazado-de-
        éxito de F1, reabierto. Ahora el except registra la talla del lote
        descargado (post-filtro tech) y la racha avanza."""
        from sqlalchemy import select

        from config import settings
        from models.source_health import SourceHealth
        from services.job_repository import JobRepository

        monkeypatch.setattr(settings, "SOURCE_HEALTH_UNSTORED_STREAK", 2)

        async def timing_out(self, hashes):
            raise RuntimeError("statement timeout en known_hashes")

        monkeypatch.setattr(JobRepository, "known_hashes", timing_out)
        now = datetime.now(timezone.utc)

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            for run in range(2):
                # Lote con ofertas fuera de ventana: la pre-pasada SÍ llama a
                # known_hashes, que revienta antes de que el bucle arranque.
                mock_providers.return_value = [
                    _make_mock_provider(
                        "ostjob",
                        [
                            self._dated_job(
                                f"Editor T{run}-{i}",
                                f"http://o.com/timeout{run}{i}",
                                now - timedelta(days=30),
                            )
                            for i in range(3)
                        ],
                    )
                ]
                summary = await _fetch_providers_async()

        assert summary["errors"] >= 1
        assert any(entry.startswith("ostjob:") for entry in summary["unhealthy"]), (
            f"la fuente no se degradó pese a perder el lote cada run: "
            f"{summary['unhealthy']}"
        )
        fila = (
            await db_session.execute(
                select(SourceHealth).where(SourceHealth.source_key == "ostjob")
            )
        ).scalar_one()
        assert fila.consecutive_unstored == 2, (
            "la racha de persistencia no se movió con el fallo pre-bucle"
        )

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_fuente_sana_que_descarta_el_80_por_ciento_en_silencio(
        self, mock_providers, monkeypatch, caplog, db_session
    ):
        """K1, caso sano — régimen estacionario de una fuente WINDOW: el 80 %
        del listado está fuera de ventana pero son RE-VISTAS (ya en corpus,
        se reconocen). Debe seguir en silencio: nada en unhealthy, ningún
        ERROR de deriva. Sin este test, el detector degeneraría en la alerta
        por ratio de descartes que se descartó a propósito."""
        from config import settings
        from services.job_repository import JobRepository

        monkeypatch.setattr(settings, "HARVEST_ALERT_MIN_BATCH", 10)
        now = datetime.now(timezone.utc)

        # El corpus ya contiene las 8 viejas del listado (se ingirieron
        # cuando eran frescas).
        viejas = [
            self._dated_job(
                f"Steady {i}", f"http://o.com/steady/{i}", now - timedelta(days=30)
            )
            for i in range(8)
        ]
        repo = JobRepository(db_session)
        for job in viejas:
            await repo.upsert_job(dict(job))
        await db_session.commit()

        nuevas = [
            self._dated_job(
                f"Fresh {i}", f"http://o.com/fresh/{i}", now - timedelta(days=1)
            )
            for i in range(2)
        ]
        mock_providers.return_value = [
            _make_mock_provider("ostjob", [*viejas, *nuevas])
        ]

        with (
            patch(
                "tasks.fetch_tasks.task_session",
                new=_mock_session_factory(db_session),
            ),
            caplog.at_level(logging.ERROR, logger="services.harvest_window"),
        ):
            summary = await _fetch_providers_async()

        assert summary["unhealthy"] == []
        assert not [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "deriva" in r.getMessage()
        ]
        # Las 8 re-vistas fuera de ventana pasaron por el upsert igualmente.
        assert summary["updated"] == 8
        assert summary["new"] == 2

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_bootstrap_sin_corpus_en_silencio(
        self, mock_providers, monkeypatch, caplog, db_session
    ):
        """K1, caso sano — primer run de una fuente WINDOW sin corpus: nada
        reconocido es lo NORMAL (no hay nada que reconocer) y las altas del
        propio run no cuentan como corpus en riesgo. Silencio."""
        from config import settings

        monkeypatch.setattr(settings, "HARVEST_ALERT_MIN_BATCH", 10)
        now = datetime.now(timezone.utc)
        lote = [
            self._dated_job(
                f"Boot {i}", f"http://o.com/boot/{i}", now - timedelta(days=1)
            )
            for i in range(12)
        ]
        mock_providers.return_value = [_make_mock_provider("ostjob", lote)]

        with (
            patch(
                "tasks.fetch_tasks.task_session",
                new=_mock_session_factory(db_session),
            ),
            caplog.at_level(logging.ERROR, logger="services.harvest_window"),
        ):
            summary = await _fetch_providers_async()

        assert summary["unhealthy"] == []
        assert not [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "deriva" in r.getMessage()
        ]

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_interruptor_apagado_comportamiento_de_hoy(
        self, mock_providers, monkeypatch, db_session
    ):
        """HARVEST_WINDOW_ENABLED=False ⇒ no se descarta nada en ningún run:
        idéntico al pipeline anterior a V.2."""
        from config import settings

        monkeypatch.setattr(settings, "HARVEST_WINDOW_ENABLED", False)
        now = datetime.now(timezone.utc)
        jobs = [
            self._dated_job("Editor Off", "http://o.com/off", now - timedelta(days=90)),
            self._dated_job("Editor OffND", "http://o.com/offnd", None),
        ]
        mock_providers.return_value = [_make_mock_provider("ostjob", jobs)]

        with patch(
            "tasks.fetch_tasks.task_session",
            new=_mock_session_factory(db_session),
        ):
            summary = await _fetch_providers_async()

        assert summary["new"] == 2
        assert summary["window_skipped"] == 0
        assert summary["window_no_date"] == 0
