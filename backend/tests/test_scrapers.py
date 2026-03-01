"""Tests for all 7 scraper normalize_job + parse_listing_page methods."""

from pathlib import Path

from bs4 import BeautifulSoup

from scrapers.financejobs import FinancejobsScraper
from scrapers.gastrojob import GastrojobScraper
from scrapers.medjobs import MedJobsScraper
from scrapers.myscience import MyScienceScraper
from scrapers.schuljobs import SchulJobsScraper
from scrapers.stelle_admin import StelleAdminScraper
from scrapers.tes import TESScraper

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
        assert (
            "/jobs/id69242-research_scientist-eth_zurich-zurich"
            in stubs[0]["detail_url"]
        )

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


# ---------------------------------------------------------------------------
# TES.com
# ---------------------------------------------------------------------------


class TestTESScraper:
    def test_source_name(self):
        assert TESScraper().get_source_name() == "tes"

    def test_parse_listing_page(self):
        html = (FIXTURES / "tes_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = TESScraper().parse_listing_page(soup)
        assert len(stubs) == 3
        assert stubs[0]["title"] == "Director of Co-Curricular Learning"
        assert stubs[0]["company"] == "Collège Alpin Beau Soleil SA"
        assert stubs[0]["location"] == "Switzerland"
        assert "/jobs/vacancy/" in stubs[0]["url"]
        assert stubs[0]["salary_original"] == "CHF 90000 - CHF 120000 per year"
        assert stubs[0]["employment_type"] == "Full Time"
        assert stubs[0]["logo"].endswith("beau-soleil-logo.png")
        # Second job has no salary
        assert stubs[1]["salary_original"] is None
        # Third job has no logo
        assert stubs[2]["logo"] is None

    def test_parse_listing_page_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert TESScraper().parse_listing_page(soup) == []

    def test_normalize_job(self):
        raw = {
            "title": "Primary Teacher",
            "company": "Zurich International School",
            "url": "https://www.tes.com/jobs/vacancy/primary-teacher-123",
            "location": "Zurich, Switzerland",
            "description": "Teach Year 3 students in our British curriculum school.",
            "employment_type": "Full Time",
            "salary_original": "CHF 90000 per year",
        }
        result = TESScraper().normalize_job(raw)
        _assert_normalized(result, "tes")
        assert result["title"] == "Primary Teacher"
        assert result["company"] == "Zurich International School"

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Teacher",
            "company": "",
            "url": "https://www.tes.com/jobs/vacancy/teacher-999",
        }
        result = TESScraper().normalize_job(raw)
        _assert_normalized(result, "tes")

    def test_fetch_details_disabled(self):
        assert TESScraper.FETCH_DETAILS is False

    def test_page_size_one(self):
        assert TESScraper.PAGE_SIZE == 1


# ---------------------------------------------------------------------------
# schuljobs.ch
# ---------------------------------------------------------------------------


class TestSchulJobsScraper:
    def test_source_name(self):
        assert SchulJobsScraper().get_source_name() == "schuljobs"

    def test_parse_listing_page(self):
        html = (FIXTURES / "schuljobs_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = SchulJobsScraper().parse_listing_page(soup)
        assert len(stubs) == 3
        assert stubs[0]["title"] == "Primarlehrperson Zyklus 2"
        assert stubs[0]["company"] == "Tagesschule. Für das Kind"
        assert stubs[0]["canton"] == "ZH"
        assert stubs[0]["location"] == "Zürich"
        assert "J954885" in stubs[0]["detail_url"]
        # Third job has relative URL
        assert stubs[2]["detail_url"].startswith("https://")

    def test_parse_listing_page_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert SchulJobsScraper().parse_listing_page(soup) == []

    def test_parse_job_detail(self):
        html = (FIXTURES / "schuljobs_detail.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        detail = SchulJobsScraper().parse_job_detail(soup)
        assert detail["title"] == "Primarlehrperson Zyklus 2"
        assert detail["company"] == "Tagesschule. Für das Kind"
        assert detail["location"] == "Zürich"
        assert detail["canton"] == "ZH"
        assert detail["employment_type"] == "PART_TIME"
        assert "Primarlehrer" in detail["description"]
        assert detail["logo"].endswith("fuerdaskind.png")

    def test_normalize_job(self):
        raw = {
            "title": "Primarlehrperson Zyklus 2",
            "company": "Tagesschule. Für das Kind",
            "url": "https://www.schuljobs.ch/job/primarlehrperson/J954885",
            "location": "Zürich",
            "canton": "ZH",
            "description": "Wir suchen eine Primarlehrperson für Zyklus 2.",
            "employment_type": "PART_TIME",
        }
        result = SchulJobsScraper().normalize_job(raw)
        _assert_normalized(result, "schuljobs")
        assert result["title"] == "Primarlehrperson Zyklus 2"
        assert result["canton"] == "ZH"

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Lehrperson",
            "url": "https://www.schuljobs.ch/job/lp/J000001",
        }
        result = SchulJobsScraper().normalize_job(raw)
        _assert_normalized(result, "schuljobs")

    def test_fetch_details_enabled(self):
        assert SchulJobsScraper.FETCH_DETAILS is True

    def test_parse_listing_page_ajax_fragment(self):
        """Verify parse_listing_page works on AJAX HTML fragments (no <html> wrapper)."""
        html_fragment = """
        <article class="jobs-job">
          <div>
            <h3>
              <a class="js-joboffer-detail"
                 href="https://www.schuljobs.ch/job/schulleiter-in/J970200">
                Schulleiter/in 80-100%
              </a>
            </h3>
            <p>BE · Bern · Bildungsdirektion Bern</p>
          </div>
        </article>
        <article class="jobs-job">
          <div>
            <h3>
              <a class="js-joboffer-detail"
                 href="https://www.schuljobs.ch/job/logopaedin/J970201">
                Logopädin 60%
              </a>
            </h3>
            <p>AG · Aarau · Schule Aarau</p>
          </div>
        </article>
        """
        soup = BeautifulSoup(html_fragment, "lxml")
        stubs = SchulJobsScraper().parse_listing_page(soup)
        assert len(stubs) == 2
        assert stubs[0]["title"] == "Schulleiter/in 80-100%"
        assert stubs[0]["company"] == "Bildungsdirektion Bern"
        assert stubs[0]["canton"] == "BE"
        assert stubs[1]["title"] == "Logopädin 60%"
        assert stubs[1]["canton"] == "AG"

    def test_searchhash_extraction(self):
        """Verify searchhash can be extracted from initial page HTML."""
        html = """
        <html><body>
        <section class="js-list-result" data-searchhash="abc123def" data-total="100">
        </section>
        <a class="btn btn-more-jobs js-btn-scroll" data-nextpage="2">
          Weitere Jobs anzeigen …
        </a>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        result_list = soup.select_one("[data-searchhash]")
        assert result_list is not None
        assert result_list.get("data-searchhash") == "abc123def"
        btn = soup.select_one("[data-nextpage]")
        assert btn is not None
        assert int(btn.get("data-nextpage")) == 2
