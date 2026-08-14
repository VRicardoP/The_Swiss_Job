"""Tests de `published_at` en normalize_job (ticket 2A / ADR-10).

Dos bloques:

1. LA PROHIBICIÓN del ticket: un payload SIN el campo de fecha del portal
   deja `published_at is None` — nunca un valor cercano a datetime.now().
   Este test es el que impide que alguien "arregle" el hueco con
   first_seen_at/last_seen_at/now().
2. Extracción correcta por FAMILIA de formato: API JSON con ISO, epoch,
   RSS con pubDate y scraper con __NEXT_DATA__.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from providers.arbeitnow import ArbeitnowProvider
from providers.ostjob import OstjobProvider
from providers.remotive import RemotiveProvider
from providers.weworkremotely import WeWorkRemotelyProvider
from scrapers.irishjobs import IrishJobsScraper
from scrapers.schuljobs import SchulJobsScraper
from scrapers.tes import TESScraper

FIXTURES = Path(__file__).parent / "fixtures"
UTC = timezone.utc


def _rss_item(pub_date: str | None) -> ET.Element:
    """Item RSS mínimo de weworkremotely, con o sin pubDate."""
    item = ET.Element("item")
    ET.SubElement(item, "title").text = "ACME Corp: Python Developer"
    ET.SubElement(item, "link").text = "https://weworkremotely.com/job/123"
    ET.SubElement(item, "description").text = "<p>Build Python APIs</p>"
    if pub_date is not None:
        ET.SubElement(item, "pubDate").text = pub_date
    return item


_CHMEDIA_RAW = {
    "title": "Sachbearbeiter",
    "company": {"name": "ACME AG"},
    "externalId": "abc-123",
    "workplaceCity": "St. Gallen",
    "cantons": ["SG"],
    "activity": "<p>Job description</p>",
}


class TestPublishedAtProhibition:
    """Payload sin fecha del portal ⇒ published_at is None, y NUNCA now()."""

    def test_arbeitnow_without_created_at(self):
        raw = {
            "title": "Dev",
            "company_name": "ACME",
            "url": "https://arbeitnow.com/job/1",
            "description": "x",
        }
        result = ArbeitnowProvider().normalize_job(raw)
        assert result["published_at"] is None

    def test_remotive_without_publication_date(self):
        raw = {
            "title": "Dev",
            "company_name": "ACME",
            "url": "https://remotive.com/job/1",
            "description": "x",
        }
        result = RemotiveProvider().normalize_job(raw)
        assert result["published_at"] is None

    def test_chmedia_without_date_first_published(self):
        result = OstjobProvider().normalize_job(dict(_CHMEDIA_RAW))
        assert result["published_at"] is None

    def test_weworkremotely_without_pubdate(self):
        result = WeWorkRemotelyProvider().normalize_job(_rss_item(None))
        assert result["published_at"] is None

    def test_irishjobs_without_date_posted(self):
        stub = {
            "title": "Legal PA",
            "company": "Lex",
            "url": "https://www.irishjobs.ie/job/1",
            "location": "Dublin",
            "description": "x",
        }
        result = IrishJobsScraper().normalize_job(stub)
        assert result["published_at"] is None

    def test_schuljobs_without_date_posted(self):
        stub = {
            "title": "Lehrperson",
            "company": "Schule X",
            "url": "https://www.schuljobs.ch/job/1",
            "location": "Zurich",
        }
        result = SchulJobsScraper().normalize_job(stub)
        assert result["published_at"] is None


class TestPublishedAtExtractionByFamily:
    """Una familia de formato real por test; el resto comparte el mismo camino."""

    def test_api_json_iso_with_offset(self):
        # Familia API JSON + ISO8601 (chmedia cubre ostjob y zentraljob).
        raw = dict(_CHMEDIA_RAW, dateFirstPublished="2026-08-14T07:00:06.318+02:00")
        result = OstjobProvider().normalize_job(raw)
        assert result["published_at"] == datetime(
            2026, 8, 14, 5, 0, 6, 318000, tzinfo=UTC
        )

    def test_api_json_epoch_seconds(self):
        raw = {
            "title": "Dev",
            "company_name": "ACME",
            "url": "https://arbeitnow.com/job/1",
            "description": "x",
            "created_at": 1786683627,
        }
        result = ArbeitnowProvider().normalize_job(raw)
        assert result["published_at"] == datetime(2026, 8, 14, 5, 0, 27, tzinfo=UTC)

    def test_rss_pubdate_rfc822(self):
        result = WeWorkRemotelyProvider().normalize_job(
            _rss_item("Thu, 13 Aug 2026 00:00:00 +0000")
        )
        assert result["published_at"] == datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)

    def test_next_data_scraper_extracts_date(self):
        # tes: advert.startDate del __NEXT_DATA__ (fixture real).
        html = (FIXTURES / "tes_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = TESScraper().parse_listing_page(soup)
        assert stubs, "fixture must yield stubs"
        result = TESScraper().normalize_job(stubs[0])
        assert result["published_at"] == datetime(2026, 2, 27, 0, 0, 0, tzinfo=UTC)
