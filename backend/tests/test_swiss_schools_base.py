"""Tests de caracterización para SwissSchoolBaseScraper (comportamiento compartido)."""

from bs4 import BeautifulSoup

from scrapers.swiss_schools_base import SwissSchoolBaseScraper


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
