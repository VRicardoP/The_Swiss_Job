"""Regresiones de la auditoría G3 — LOTE D: los DOS FLANCOS GEMELOS de G2.

Los tests de G2 dan por cerrados dos bugs que siguen vivos porque ejercitan la
unidad AISLADA en vez del camino real. Aquí las mordidas recorren el camino
real:

- **P2-1 · `scrapers/irishjobs.py`** — el fix G2/P2-1 marcó
  `_stop_reason = "error"` en los cortes por fallo de `_harvest_host`, pero
  `_scrape_with_httpx` llama a `_harvest_host` UNA VEZ POR HOST sobre el MISMO
  atributo: el `known_page` del host 2 BORRABA el `error` del host 1, el guard
  de `tasks/scraping_tasks.py:355` pasaba y el cursor AVANZABA aprendiendo una
  pasada incompleta — las ofertas de la página 2+ (newest-first) no se
  descargaban jamás. NAE/Inspired sí recibieron el acumulador `had_error`
  (G2/P3-1); irishjobs no. `tests/test_g2_fix_cursor_scrapers.py` ejercita
  `_harvest_host` AISLADO, así que el flanco le es invisible: aquí se recorre
  el BUCLE COMPLETO de dos hosts y, además, el task real hasta el guard.

- **P2-2 · `services/job_classifier.py`** — el guard `all_caps` del fix
  G2/P3-2 se calculaba sobre `title + tags` y era INERTE en producción: basta
  UN tag en minúsculas y TODAS las fuentes emiten tags así (la watchlist,
  literalmente `["education", "international school", <id>]`). Los tests de G2
  usan `tags=[]`; aquí se entra por el camino real (`DataNormalizer`) con tags
  REALISTAS y se llega hasta `teacher_alert.is_primary_teacher_job`, que es
  donde se pierde el email de alerta.
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from config import settings
from models.source_cursor import SourceCursor
from scrapers.irishjobs import IrishJobsScraper
from services.data_normalizer import DataNormalizer
from services.teacher_alert import is_primary_teacher_job
from tasks.scraping_tasks import _fetch_scrapers_async
from utils import fetch_diagnostics as diag

# ---------------------------------------------------------------------------
# P2-1 — utillería del bucle de DOS hosts
# ---------------------------------------------------------------------------

HOST_1 = "https://www.irishjobs.ie"
HOST_2 = "https://www.jobs.ie"

# 25 = PAGE_SIZE de StepStone: una página LLENA obliga a pedir la siguiente.
_FULL_PAGE = 25


def _recent_iso() -> str:
    """Fecha dentro de la ventana de cosecha (si no, el job ni se intenta)."""
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _job_paths(prefix: str, count: int = _FULL_PAGE) -> list[str]:
    return [f"/job/{prefix}-{i}" for i in range(count)]


def _urls(host: str, prefix: str, count: int = _FULL_PAGE) -> list[str]:
    return [host + path for path in _job_paths(prefix, count)]


def _identities(first_id: int, count: int = _FULL_PAGE) -> list[str]:
    """Identidad de cursor de irishjobs = id de PLATAFORMA (G1/P3-7), no la
    URL: la misma oferta vive en los dos hosts con URLs distintas."""
    return [f"irishjobs:{first_id + i}" for i in range(count)]


def _page(host_prefix: str, first_id: int, count: int = _FULL_PAGE) -> MagicMock:
    """Respuesta SSR de StepStone con `count` ofertas parseables."""
    items = [
        {
            "id": first_id + i,
            "title": "Primary Teacher",
            "url": path,
            "companyName": "Dublin School",
            "location": "Dublin",
            "textSnippet": "Teaching role in a Dublin primary school.",
            "datePosted": _recent_iso(),
        }
        for i, path in enumerate(_job_paths(host_prefix, count))
    ]
    blob = json.dumps({"searchResults": {"items": items, "meta": {"total": 999}}})
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


def _client_factory(client: MagicMock) -> MagicMock:
    """Sustituto de `httpx.AsyncClient(...)` utilizable como context manager."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _host1_falla_host2_conocido() -> MagicMock:
    """Cliente del escenario: host1 cosecha la pág.1 y cae en la 2; host2
    sirve una página ENTERA ya conocida por el cursor (early-stop)."""
    client = MagicMock()
    client.get = AsyncMock(
        side_effect=[
            _page("ie", first_id=1),  # host1 pág.1 — 25 ofertas NUEVAS
            httpx.ConnectTimeout("boom"),  # host1 pág.2 — se cae
            _page("js", first_id=101),  # host2 pág.1 — entera conocida
        ]
    )
    return client


def _mock_session_factory(db_session):
    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


async def _seed_cursor(db_session, source: str, identities: list[str]) -> None:
    """Cursor ya bootstrapeado, como el de una fuente en régimen."""
    cursor = SourceCursor(
        source_key=source,
        scope_key="default",
        recent_identities=identities,
    )
    cursor.bootstrap_complete = True
    cursor.avg_new_jobs_per_run = 5.0
    cursor.avg_pages_per_run = 2.0
    cursor.consecutive_empty_runs = 0
    db_session.add(cursor)
    await db_session.commit()


async def _load_cursor(db_session, source: str) -> SourceCursor:
    return (
        await db_session.execute(
            select(SourceCursor).where(SourceCursor.source_key == source)
        )
    ).scalar_one()


class TestP21IrishjobsBucleDeDosHosts:
    """El bucle COMPLETO de `_scrape_with_httpx` (los dos hosts), no
    `_harvest_host` aislado como en G2."""

    async def test_el_known_page_del_host_2_no_borra_el_error_del_host_1(self):
        scraper = _irish_scraper()
        # El cursor ya conoce TODO el host 2 → su página 1 es `known_page`.
        scraper._known_urls = set(_identities(101))
        diag.begin()

        with patch(
            "scrapers.irishjobs.httpx.AsyncClient",
            _client_factory(_host1_falla_host2_conocido()),
        ):
            stubs = await scraper._scrape_with_httpx("")

        # Degradación parcial por diseño: lo cosechado se conserva.
        assert len(stubs) == 2 * _FULL_PAGE
        assert scraper._stop_reason == "error", (
            "el 'error' del host 1 lo borró el 'known_page' del host 2: el run "
            "terminó «con hambre» y el cursor no debe aprender nada"
        )

    async def test_dos_hosts_limpios_no_inventan_error(self):
        """No-regresión: fin de listado legítimo en ambos hosts."""
        scraper = _irish_scraper()
        client = MagicMock()
        client.get = AsyncMock(
            side_effect=[_page("ie", 1, count=3), _page("js", 101, count=3)]
        )
        diag.begin()

        with patch("scrapers.irishjobs.httpx.AsyncClient", _client_factory(client)):
            stubs = await scraper._scrape_with_httpx("")

        assert len(stubs) == 6
        assert scraper._stop_reason is None


class TestP21GuardDelCursorEndToEnd:
    """Camino REAL completo: `_fetch_scrapers_async` → `fetch_jobs` → el bucle
    de dos hosts → el guard de `tasks/scraping_tasks.py:355` → el cursor en
    BD. Es lo que ninguna prueba de G2 recorría."""

    SOURCE = "irishjobs"

    @asynccontextmanager
    async def _entorno(self, db_session, client):
        with (
            patch("scrapers.irishjobs.httpx.AsyncClient", _client_factory(client)),
            # Compliance vive en otra capa (tiene sus propios tests) y aquí
            # solo estorbaría: la fuente está autorizada.
            patch.object(IrishJobsScraper, "_pre_check", AsyncMock(return_value=True)),
            patch.object(IrishJobsScraper, "_reset_compliance_blocks", AsyncMock()),
            patch(
                "tasks.scraping_tasks.task_session",
                new=_mock_session_factory(db_session),
            ),
        ):
            yield

    async def _run(self, db_session, monkeypatch, client) -> IrishJobsScraper:
        monkeypatch.setattr(settings, "CURSOR_INCREMENTAL_ENABLED", True)
        monkeypatch.setattr(settings, "CRAWLER_BUDGET_ENABLED", False)
        scraper = _irish_scraper()
        with patch("tasks.scraping_tasks.get_all_scrapers", return_value=[scraper]):
            async with self._entorno(db_session, client):
                await _fetch_scrapers_async()
        return scraper

    async def test_el_cursor_no_aprende_la_pasada_incompleta(
        self, monkeypatch, db_session
    ):
        await _seed_cursor(db_session, self.SOURCE, _identities(101))

        scraper = await self._run(
            db_session, monkeypatch, _host1_falla_host2_conocido()
        )

        assert scraper._stop_reason == "error"
        cursor = await _load_cursor(db_session, self.SOURCE)
        aprendidas = [i for i in _identities(1) if i in cursor.recent_identities]
        assert aprendidas == [], (
            "el guard de scraping_tasks:355 dejó avanzar el cursor con la "
            "página 1 de irishjobs.ie: el run siguiente hará early-stop ahí y "
            "las ofertas de la página 2+ no se descargarán JAMÁS"
        )

    async def test_run_limpio_si_deja_avanzar_el_cursor(self, monkeypatch, db_session):
        """No-regresión del guard: sin fallo, el cursor SÍ debe aprender."""
        await _seed_cursor(db_session, self.SOURCE, [])
        client = MagicMock()
        client.get = AsyncMock(
            side_effect=[_page("ie", 1, count=3), _page("js", 101, count=3)]
        )

        scraper = await self._run(db_session, monkeypatch, client)

        assert scraper._stop_reason is None
        cursor = await _load_cursor(db_session, self.SOURCE)
        assert set(_identities(1, 3)) <= set(cursor.recent_identities)


# ---------------------------------------------------------------------------
# P2-2 — el clasificador por el camino real, con TAGS de producción
# ---------------------------------------------------------------------------

# Literal de `scrapers/swiss_schools_nae.py:142` (ídem inspired/hautlac/iscs).
WATCHLIST_TAGS = ["education", "international school", "nae_zurich"]
# Salida real de `extract_job_skills`, que es de donde salen los tags de
# publicjobs / zebis / schuljobs / tes / irishjobs / gastrojob.
SKILL_TAGS = ["english", "french", "microsoft office"]


def _categoria(title: str, tags: list[str]) -> str:
    """Camino real de ingesta: el clasificador NO se llama nunca a pelo."""
    return DataNormalizer.classify_category({"title": title, "tags": tags})["category"]


class TestP22ClasificadorConTagsReales:
    @pytest.mark.parametrize(
        "title,tags",
        [
            ("ENSEIGNANTE PRIMAIRE POUR UN REMPLACEMENT", WATCHLIST_TAGS),
            ("ENSEIGNANT-E PRIMAIRE POUR UN REMPLACEMENT", WATCHLIST_TAGS),
            ("UN-E ENSEIGNANT-E PRIMAIRE", SKILL_TAGS),
            ("UNO INSTRUCTOR PER LA SCUOLA ELEMENTARE", SKILL_TAGS),
        ],
    )
    def test_el_articulo_no_secuestra_la_docencia_con_tags(self, title, tags):
        """Con un solo tag en minúsculas el guard de G2 dejaba de existir."""
        assert _categoria(title, tags) == "H", (
            "el guard all_caps se calculaba sobre título+tags: cualquier tag "
            "en minúsculas lo desactivaba y el token UN volvía a ganar"
        )

    @pytest.mark.parametrize(
        "title,tags",
        [
            ("UN VOLUNTEER PROGRAMME OFFICER", []),
            ("WHO MEDICAL OFFICER GENEVA", []),
            ("ILO SENIOR ECONOMIST", []),
        ],
    )
    def test_el_organismo_real_en_mayusculas_sigue_siendo_f(self, title, tags):
        """Dirección contraria del trade-off: con el guard de G2 activo estos
        títulos caían en 'otros'. El acrónimo decide cuando NADA más casa."""
        assert _categoria(title, tags) == "F"

    @pytest.mark.parametrize(
        "title",
        ["UN Volunteer Programme Officer", "ILO Senior Economist"],
    )
    def test_el_organismo_en_titulo_mixto_sigue_siendo_f(self, title):
        """No-regresión de G1/P2-9."""
        assert _categoria(title, WATCHLIST_TAGS) == "F"

    def test_el_acronimo_en_minusculas_sigue_sin_casar(self):
        """No-regresión: «un» minúscula nunca fue el organismo."""
        assert _categoria("Un poste de developpeur backend", SKILL_TAGS) != "F"


class TestP22AlertaProfesorDePrimaria:
    """Mordida end-to-end: normalización real → categoría → teacher_alert."""

    @staticmethod
    def _job(title: str, tags: list[str]) -> dict:
        return {
            "hash": "h" * 32,
            "source": "swiss_schools_nae",
            "title": title,
            "company": "NAE Zurich",
            "url": "https://example.test/job/1",
            "location": "Zurich",
            "canton": "ZH",
            "description": "Poste au sein d'une ecole primaire internationale.",
            "description_snippet": "Poste au sein d'une ecole primaire...",
            "remote": False,
            "tags": tags,
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

    @pytest.mark.parametrize(
        "title,tags",
        [
            ("ENSEIGNANTE PRIMAIRE POUR UN REMPLACEMENT", WATCHLIST_TAGS),
            ("UNO INSTRUCTOR PER LA SCUOLA ELEMENTARE", SKILL_TAGS),
        ],
    )
    def test_la_alerta_vuelve_a_dispararse(self, title, tags):
        job = DataNormalizer.normalize(self._job(title, tags))

        assert job["category"] == "H"
        assert (
            is_primary_teacher_job(job["category"], job["title"], job["tags"]) is True
        ), (
            "categoría F ⇒ is_primary_teacher_job corta en la primera línea y "
            "el email de alerta (caso de uso central) no se envía"
        )
