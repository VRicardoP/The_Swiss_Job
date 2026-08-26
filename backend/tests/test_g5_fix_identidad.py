"""G5/P1-1 — la RAMA MUDA de la deriva de identidad: el clon con id NUEVO.

La deriva que motivó la canonización de `arbeitnow`/`jobgether` tiene DOS
salidas, no una, y dependen de si el portal re-lista la vacante en la misma URL
o en una nueva:

- **misma url** ⇒ `UniqueViolationError` sobre `ix_jobs_url` ⇒
  `JobIdentityConflictError` ⇒ `identity_conflicts` (rama que G4 sí cubrió);
- **url NUEVA** (el id volátil cambia — que es exactamente el fenómeno que
  motivó canonizar) ⇒ **ningún choque**: el INSERT tiene ÉXITO, entra una fila
  CLON y la histórica deja de refrescar `last_seen_at` para siempre.

La segunda no la veía nadie: ni `identity_conflicts` (no hay excepción), ni
`Deduplicator.find_fuzzy_duplicate` (excluye a propósito los pares de la misma
fuente), ni `harvest_window.watch_drift` (exige `recognized == 0` y esta deriva
es PARCIAL). El run salía `new=1, errors=0, identity_conflicts=0, unhealthy=[]`
con dos filas para una sola vacante — medido en producción: 388 clones de
arbeitnow, 81 de jobgether, 55 grupos con clon nuevo solo el 2026-08-25.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select, text

from config import settings
from models.job import Job
from services.deduplicator import Deduplicator
from services.job_repository import JobRepository
from services.job_service import BaseJobProvider
from tasks.fetch_tasks import _fetch_providers_async
from tasks.scraping_tasks import _fetch_scrapers_async

# Las dos URLs son la MISMA vacante de arbeitnow: solo cambia el id volátil
# final, que es justo lo que `canonical_identity_url` descarta.
_URL_VIEJA = "https://www.arbeitnow.com/jobs/companies/acme/python-engineer-bern-459633"
_URL_NUEVA = "https://www.arbeitnow.com/jobs/companies/acme/python-engineer-bern-198909"


def _job(job_hash: str, source: str, url: str) -> dict:
    return {
        "hash": job_hash,
        "source": source,
        "title": "Senior Python Engineer",
        "company": "Acme GmbH",
        "url": url,
        "published_at": datetime.now(timezone.utc),
        "location": "Bern",
        "canton": "BE",
        "description": "Eine spannende Stelle als Python Entwickler in Bern.",
        "description_snippet": "Eine spannende...",
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


async def _sembrar_historica(db_session, source: str, *, antigua: bool = True) -> None:
    """La fila que ya está en el corpus, con el hash PRE-canonización.

    G6/P3-3 — `first_seen_at` se retrasa a propósito: la alarma distingue ahora
    la fila HISTÓRICA (que dejará de refrescarse: eso es la deriva) de la gemela
    que entró en la MISMA corrida (dos plazas simultáneas, que no lo es). Estos
    tests dicen «histórica» en su nombre, así que la siembran como tal.
    """
    repo = JobRepository(db_session)
    job = _job("a" * 32, source, _URL_VIEJA)
    job["fuzzy_hash"] = Deduplicator.compute_fuzzy_hash(job["title"], job["company"])
    await repo.upsert_job(job)
    if antigua:
        await db_session.execute(
            text(
                "UPDATE jobs SET first_seen_at = now() - interval '2 days' "
                "WHERE hash = :h"
            ),
            {"h": "a" * 32},
        )
    await db_session.commit()


# G7/P3-6: `find_same_source_clone` recibe el instante de arranque de la corrida
# en vez de aproximarlo con `now() - 1 h`. En los tests, la corrida «arranca»
# ahora: la sembrada como antigua (2 días) es histórica y la sembrada en este
# mismo momento no lo es.
_ARRANQUE = datetime.now(timezone.utc)


class TestRamaMudaDeLaDeriva:
    async def test_la_consulta_gemela_ve_el_clon_intra_fuente(self, db_session):
        """`find_fuzzy_duplicate` excluye la misma fuente; la alarma NO."""
        await _sembrar_historica(db_session, "arbeitnow")
        fuzzy = Deduplicator.compute_fuzzy_hash("Senior Python Engineer", "Acme GmbH")

        # La vía de MARCADO sigue ciega a propósito (G3): nada que reprochar.
        assert (
            await Deduplicator.find_fuzzy_duplicate(db_session, fuzzy, "arbeitnow")
        ) is None
        # La vía de ALARMA sí lo ve, lo marca como HISTÓRICO, y no confunde a la
        # fila con ella misma.
        assert (
            await Deduplicator.find_same_source_clone(
                db_session, fuzzy, "arbeitnow", "b" * 32, _ARRANQUE
            )
        ) == ("a" * 32, True)
        assert (
            await Deduplicator.find_same_source_clone(
                db_session, fuzzy, "arbeitnow", "a" * 32, _ARRANQUE
            )
        ) is None

    async def test_la_gemela_de_la_MISMA_corrida_no_se_marca_como_historica(
        self, db_session
    ):
        """G6/P3-3 — 32,5 % de los grupos gemelos son plazas simultáneas."""
        await _sembrar_historica(db_session, "arbeitnow", antigua=False)
        fuzzy = Deduplicator.compute_fuzzy_hash("Senior Python Engineer", "Acme GmbH")

        # Se DETECTA igual (perder esto costaría 61 de los 406 clones reales)…
        gemela = await Deduplicator.find_same_source_clone(
            db_session, fuzzy, "arbeitnow", "b" * 32, _ARRANQUE
        )
        assert gemela is not None
        # …pero NO se llama histórica, que es lo que el diagnóstico afirmaba.
        assert gemela == ("a" * 32, False)

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_providers_el_clon_con_id_nuevo_deja_de_ser_mudo(
        self, mock_providers, db_session
    ):
        """El INSERT tiene ÉXITO (url distinta) y aun así el run debe gritar."""
        await _sembrar_historica(db_session, "arbeitnow")

        mock_providers.return_value = [
            _make_provider("arbeitnow", [_job("b" * 32, "arbeitnow", _URL_NUEVA)])
        ]
        with patch(
            "tasks.fetch_tasks.task_session", new=_mock_session_factory(db_session)
        ):
            summary = await _fetch_providers_async()

        # El síntoma que hace muda a esta rama: NO hay choque ni error.
        assert summary["errors"] == 0
        assert summary["identity_conflicts"] == 0
        assert summary["new"] == 1
        # Y sin embargo hay dos filas para UNA sola vacante.
        hashes = sorted((await db_session.execute(select(Job.hash))).scalars())
        assert hashes == ["a" * 32, "b" * 32]

        assert summary.get("identity_clones") == 1, (
            "el clon con id NUEVO entró sin que ningún contador se enterase: "
            "la rama DOMINANTE de la deriva sigue siendo muda"
        )
        assert any("DERIVA DE IDENTIDAD" in m for m in summary["unhealthy"]), (
            "la fuente sale SANA mientras duplica el corpus y abandona la "
            "fila histórica"
        )
        # La alarma NO desactiva ni marca: solo observa.
        rows = (
            await db_session.execute(select(Job.hash, Job.is_active, Job.duplicate_of))
        ).all()
        assert all(activa and dup is None for _, activa, dup in rows)

    @patch("tasks.scraping_tasks.get_all_scrapers")
    async def test_scrapers_el_clon_con_id_nuevo_deja_de_ser_mudo(
        self, mock_scrapers, monkeypatch, db_session
    ):
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", False)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", False)
        await _sembrar_historica(db_session, "scr_clon")

        mock_scrapers.return_value = [
            _make_scraper("scr_clon", [_job("b" * 32, "scr_clon", _URL_NUEVA)])
        ]
        with patch(
            "tasks.scraping_tasks.task_session", new=_mock_session_factory(db_session)
        ):
            summary = await _fetch_scrapers_async()

        assert summary["errors"] == 0
        assert summary["identity_conflicts"] == 0
        assert summary.get("identity_clones") == 1
        assert any("DERIVA DE IDENTIDAD" in m for m in summary["unhealthy"])

    @patch("tasks.fetch_tasks.get_all_providers")
    async def test_una_cosecha_normal_no_dispara_la_alarma(
        self, mock_providers, db_session
    ):
        """Guardarraíl: sin clon no hay alarma (ni con la re-vista del mismo
        hash, ni con una vacante distinta de la misma fuente)."""
        await _sembrar_historica(db_session, "arbeitnow")

        otra = _job("c" * 32, "arbeitnow", "https://www.arbeitnow.com/jobs/x-1")
        otra["title"] = "Data Engineer"
        mock_providers.return_value = [
            _make_provider(
                "arbeitnow",
                [_job("a" * 32, "arbeitnow", _URL_VIEJA), otra],
            )
        ]
        with patch(
            "tasks.fetch_tasks.task_session", new=_mock_session_factory(db_session)
        ):
            summary = await _fetch_providers_async()

        assert summary["identity_clones"] == 0
        assert summary["unhealthy"] == []
