"""Tests de caracterización para SwissSchoolBaseScraper (comportamiento compartido)."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from scrapers.swiss_schools_base import SwissSchoolBaseScraper
from scrapers.swiss_schools_inspired import SwissSchoolsInspiredScraper
from scrapers.swiss_schools_nae import SwissSchoolsNAEScraper


class _ConcreteSchool(SwissSchoolBaseScraper):
    """Subclase mínima para poder instanciar (los parsers son abstractos)."""

    SOURCE_NAME = "swiss_schools_test"

    def build_listing_url(self, page: int, query: str) -> str:
        return "https://example.ch"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        return []


class TestSwissSchoolBase:
    def test_fetch_details_disabled(self):
        assert _ConcreteSchool.FETCH_DETAILS is False

    def test_parse_job_detail_is_noop(self):
        assert _ConcreteSchool().parse_job_detail(BeautifulSoup("", "lxml")) == {}

    def test_normalize_adds_hash_preserving_fields(self):
        scraper = _ConcreteSchool()
        raw = {"title": "Primary Teacher", "company": "PS X", "url": "https://x.ch/1"}
        out = scraper.normalize_job(raw)
        # Añade hash sin tocar el resto del stub.
        assert out["hash"] == scraper.compute_hash(
            "Primary Teacher", "PS X", "https://x.ch/1"
        )
        assert out["title"] == "Primary Teacher"
        assert out["company"] == "PS X"
        assert out["url"] == "https://x.ch/1"

    def test_normalize_hash_is_deterministic(self):
        scraper = _ConcreteSchool()
        a = scraper.normalize_job({"title": "T", "company": "C", "url": "u"})["hash"]
        b = scraper.normalize_job({"title": "T", "company": "C", "url": "u"})["hash"]
        assert a == b


@contextmanager
def _mock_compliance(mock_engine):
    """Intercepta task_session + ComplianceEngine para todo el flujo de fetch_jobs."""
    with patch("database.task_session") as mock_ts:
        mock_ts.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_ts.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("services.compliance.ComplianceEngine", return_value=mock_engine):
            yield


def _resp(status_code: int, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    return r


# Página de búsqueda 200 sin resultados y sin marcador anti-bot: para estas
# watchlist (0 vacantes durante meses) es el estado NORMAL.
_EMPTY_SEARCH_HTML = "<html><body><p>No jobs match your search.</p></body></html>"


@pytest.mark.parametrize(
    "scraper_cls",
    [SwissSchoolsNAEScraper, SwissSchoolsInspiredScraper],
    ids=["nae", "inspired"],
)
class TestSchoolScrapersRehabilitation:
    """VD.4a (H-1): nae/inspired heredan la rehabilitación por vacío verificado.

    Sus antiguos overrides de fetch_jobs conservaban `if results:` para llamar a
    reset_blocks — con 0 vacantes durante meses (lo normal aquí), una fuente
    apagada por el kill-switch no se rehabilitaba jamás, igual que le pasó a ISB.
    """

    def _engine(self) -> AsyncMock:
        engine = AsyncMock()
        engine.can_scrape = AsyncMock(return_value=True)
        return engine

    @pytest.mark.asyncio
    async def test_empty_clean_run_resets_blocks(self, scraper_cls):
        # Run con 0 vacantes en TODOS los colegios y sin bloqueo → rehabilita.
        scraper = scraper_cls()
        engine = self._engine()

        with _mock_compliance(engine):
            with patch.object(
                scraper._circuit,
                "call",
                new_callable=AsyncMock,
                return_value=_resp(200, _EMPTY_SEARCH_HTML),
            ):
                result = await scraper.fetch_jobs("teacher")

        assert result == []
        engine.report_block.assert_not_called()
        engine.reset_blocks.assert_called_once_with(scraper_cls.SOURCE_NAME)

    @pytest.mark.asyncio
    async def test_blocked_run_does_not_reset_blocks(self, scraper_cls):
        # Si un colegio devuelve 403, el run reporta el bloqueo y NO rehabilita.
        scraper = scraper_cls()
        engine = self._engine()

        with _mock_compliance(engine):
            with patch.object(
                scraper._circuit,
                "call",
                new_callable=AsyncMock,
                return_value=_resp(403),
            ):
                result = await scraper.fetch_jobs("teacher")

        assert result == []
        engine.report_block.assert_called_with(scraper_cls.SOURCE_NAME, 403)
        engine.reset_blocks.assert_not_called()
