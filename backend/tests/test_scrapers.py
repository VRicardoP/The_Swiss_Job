"""Tests for all 5 scraper normalize_job + parse_listing_page methods."""

from pathlib import Path

from bs4 import BeautifulSoup

from scrapers.financejobs import FinancejobsScraper
from scrapers.gastrojob import GastrojobScraper
from scrapers.medjobs import MedJobsScraper
from scrapers.myscience import MyScienceScraper
from scrapers.stelle_admin import StelleAdminScraper

FIXTURES = Path(__file__).parent / "fixtures"


def _assert_normalized(result: dict, source: str) -> None:
    """Common assertions for all normalized job dicts."""
    assert result["source"] == source
    assert result["hash"]
    assert len(result["hash"]) == 32
    assert result["title"]
    assert result["url"]
    assert isinstance(result["tags"], list)
    assert len(result["tags"]) <= 15
    assert isinstance(result["remote"], bool)
    for key in [
        "company",
        "location",
        "canton",
        "description",
        "description_snippet",
        "salary_min_chf",
        "salary_max_chf",
        "salary_original",
        "salary_currency",
        "salary_period",
        "language",
        "seniority",
        "contract_type",
        "employment_type",
        "logo",
    ]:
        assert key in result


# ---------------------------------------------------------------------------
# myScience.ch
# ---------------------------------------------------------------------------


class TestMyScienceScraper:
    def test_source_name(self):
        assert MyScienceScraper().get_source_name() == "myscience"

    def test_parse_listing_page(self):
        html = (FIXTURES / "myscience_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = MyScienceScraper().parse_listing_page(soup)
        assert len(stubs) == 3
        assert stubs[0]["title"] == "Research Scientist in Machine Learning"
        assert stubs[0]["company"] == "ETH Zurich"
        assert stubs[0]["location"] == "Zurich"
        assert "detail_url" in stubs[0]
        assert "/jobs/id69242-research_scientist-eth_zurich-zurich" in stubs[0]["detail_url"]

    def test_parse_listing_page_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert MyScienceScraper().parse_listing_page(soup) == []

    def test_parse_job_detail(self):
        html = (FIXTURES / "myscience_detail.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        detail = MyScienceScraper().parse_job_detail(soup)
        assert "description" in detail
        assert "machine learning" in detail["description"].lower()
        assert detail.get("logo", "").endswith("ethz.svg")
        assert detail.get("location") == "Zurich, Rämistrasse 101"
        assert detail.get("employment_type") == "80% - 100%"

    def test_normalize_job(self):
        raw = {
            "title": "Research Scientist",
            "company": "ETH Zurich",
            "url": "https://myscience.ch/jobs/id123",
            "location": "Zurich",
            "description": "Conduct ML research with Python and PyTorch.",
        }
        result = MyScienceScraper().normalize_job(raw)
        _assert_normalized(result, "myscience")
        assert result["title"] == "Research Scientist"
        assert result["company"] == "ETH Zurich"

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Postdoc",
            "company": "",
            "url": "https://myscience.ch/jobs/id999",
        }
        result = MyScienceScraper().normalize_job(raw)
        _assert_normalized(result, "myscience")

    def test_build_listing_url(self):
        s = MyScienceScraper()
        assert s.build_listing_url(1, "") == "https://www.myscience.ch/jobs?p=1"
        assert s.build_listing_url(3, "physics") == "https://www.myscience.ch/jobs?p=3"


# ---------------------------------------------------------------------------
# Financejobs.ch
# ---------------------------------------------------------------------------


class TestFinancejobsScraper:
    def test_source_name(self):
        assert FinancejobsScraper().get_source_name() == "financejobs"

    def test_parse_listing_page_from_next_data(self):
        html = (FIXTURES / "financejobs_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = FinancejobsScraper().parse_listing_page(soup)
        assert len(stubs) == 3
        assert stubs[0]["title"] == "Senior Financial Analyst"
        assert stubs[0]["company"] == "UBS Group AG"
        assert "Zurich" in stubs[0]["location"]
        assert "/de/job/1846066401" in stubs[0]["url"]
        assert stubs[0]["salary_original"] == "120000-150000 CHF"

    def test_parse_listing_page_no_next_data(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert FinancejobsScraper().parse_listing_page(soup) == []

    def test_normalize_job(self):
        raw = {
            "title": "Portfolio Manager",
            "company": "Pictet",
            "url": "https://financejobs.ch/de/job/123",
            "location": "Geneva",
            "description": "Manage client portfolios in wealth management.",
            "salary_original": "150000 CHF",
            "employment_type": "Full-time",
        }
        result = FinancejobsScraper().normalize_job(raw)
        _assert_normalized(result, "financejobs")
        assert result["salary_original"] == "150000 CHF"

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Analyst",
            "company": "",
            "url": "https://financejobs.ch/de/job/456",
        }
        result = FinancejobsScraper().normalize_job(raw)
        _assert_normalized(result, "financejobs")

    def test_fetch_details_disabled(self):
        assert FinancejobsScraper.FETCH_DETAILS is False


# ---------------------------------------------------------------------------
# Gastrojob.ch
# ---------------------------------------------------------------------------


class TestGastrojobScraper:
    def test_source_name(self):
        assert GastrojobScraper().get_source_name() == "gastrojob"

    def test_parse_listing_page(self):
        html = (FIXTURES / "gastrojob_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = GastrojobScraper().parse_listing_page(soup)
        assert len(stubs) == 2
        assert "Küchenchef" in stubs[0]["title"]
        assert stubs[0]["company"] == "Hotel Bellevue"
        assert stubs[0]["location"] == "Bern"

    def test_parse_listing_page_empty(self):
        html = "<html><body><div>0 Stellen gefunden</div></body></html>"
        soup = BeautifulSoup(html, "lxml")
        assert GastrojobScraper().parse_listing_page(soup) == []

    def test_normalize_job(self):
        raw = {
            "title": "Sous Chef",
            "company": "Grand Hotel Zermatt",
            "url": "https://gastrojob.ch/stelle/789",
            "location": "Zermatt",
            "description": "Lead the kitchen brigade for our 5-star restaurant.",
        }
        result = GastrojobScraper().normalize_job(raw)
        _assert_normalized(result, "gastrojob")

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Koch",
            "company": "",
            "url": "https://gastrojob.ch/stelle/111",
        }
        result = GastrojobScraper().normalize_job(raw)
        _assert_normalized(result, "gastrojob")


# ---------------------------------------------------------------------------
# med-jobs.com
# ---------------------------------------------------------------------------


class TestMedJobsScraper:
    def test_source_name(self):
        assert MedJobsScraper().get_source_name() == "medjobs"

    def test_parse_listing_page(self):
        html = (FIXTURES / "medjobs_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = MedJobsScraper().parse_listing_page(soup)
        assert len(stubs) == 3
        assert "Oberärztin" in stubs[0]["title"]
        assert stubs[0]["company"] == "Universitätsspital Zürich"

    def test_parse_listing_page_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert MedJobsScraper().parse_listing_page(soup) == []

    def test_normalize_job(self):
        raw = {
            "title": "Facharzt Chirurgie",
            "company": "Kantonsspital St. Gallen",
            "url": "https://med-jobs.com/de/stelle/123",
            "location": "St. Gallen",
            "description": "Facharzt für allgemeine Chirurgie gesucht.",
        }
        result = MedJobsScraper().normalize_job(raw)
        _assert_normalized(result, "medjobs")

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Arzt",
            "company": "",
            "url": "https://med-jobs.com/de/stelle/456",
        }
        result = MedJobsScraper().normalize_job(raw)
        _assert_normalized(result, "medjobs")

    def test_conservative_rate_limit(self):
        assert MedJobsScraper.RATE_LIMIT_SECONDS >= 3.0

    def test_needs_playwright(self):
        assert MedJobsScraper.NEEDS_PLAYWRIGHT is True


# ---------------------------------------------------------------------------
# stelle.admin.ch (jobs.admin.ch)
# ---------------------------------------------------------------------------


class TestStelleAdminScraper:
    def test_source_name(self):
        assert StelleAdminScraper().get_source_name() == "stelle_admin"

    def test_needs_playwright(self):
        assert StelleAdminScraper.NEEDS_PLAYWRIGHT is True

    def test_parse_listing_page(self):
        html = (FIXTURES / "stelle_admin_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = StelleAdminScraper().parse_listing_page(soup)
        assert len(stubs) == 2
        assert "Informatiker" in stubs[0]["title"]
        assert stubs[0]["location"] == "Bern"
        assert stubs[0]["employment_type"] == "80-100%"

    def test_parse_listing_page_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert StelleAdminScraper().parse_listing_page(soup) == []

    def test_normalize_job(self):
        raw = {
            "title": "IT Projektleiter/in",
            "company": "Bundesamt für Informatik",
            "url": "https://jobs.admin.ch/job/xyz?lang=de",
            "location": "Bern",
            "description": "Leitung von IT-Projekten der Bundesverwaltung.",
            "employment_type": "100%",
        }
        result = StelleAdminScraper().normalize_job(raw)
        _assert_normalized(result, "stelle_admin")
        assert result["employment_type"] == "100%"

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Sachbearbeiter/in",
            "url": "https://jobs.admin.ch/job/abc",
        }
        result = StelleAdminScraper().normalize_job(raw)
        _assert_normalized(result, "stelle_admin")
        assert result["company"] == "Swiss Federal Administration"

    def test_fetch_details_disabled(self):
        assert StelleAdminScraper.FETCH_DETAILS is False
