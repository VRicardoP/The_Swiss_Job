"""Publication date behavior for a scraper still owned by SwissJob."""

from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from scrapers.schuljobs import SchulJobsScraper


FIXTURES = Path(__file__).parent / "fixtures"


def test_missing_date_is_not_synthesized():
    raw = {"title": "Teacher", "url": "https://example.test/job"}
    assert SchulJobsScraper().normalize_job(raw)["published_at"] is None


def test_detail_date_is_extracted():
    soup = BeautifulSoup(
        (FIXTURES / "schuljobs_detail.html").read_text(),
        "lxml",
    )
    detail = SchulJobsScraper().parse_job_detail(soup)
    result = SchulJobsScraper().normalize_job(detail)
    assert result["published_at"] == datetime(2026, 2, 27, tzinfo=timezone.utc)
