"""Tests for all 16 provider normalize_job methods."""

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from providers.adzuna import AdzunaProvider
from providers.arbeitnow import ArbeitnowProvider
from providers.careerjet import CareerjetProvider
from providers.himalayas import HimalayasProvider
from providers.ictjobs import ICTJobsProvider
from providers.jobicy import JobicyProvider
from providers.jooble import JoobleProvider
from providers.jsearch import JSearchProvider
from providers.ostjob import OstjobProvider
from providers.publicjobs import PublicJobsProvider
from providers.remoteok import RemoteOKProvider
from providers.remotive import RemotiveProvider
from providers.swisstechjobs import SwissTechJobsProvider
from providers.weworkremotely import WeWorkRemotelyProvider
from providers.zebis import ZebisProvider
from providers.zentraljob import ZentraljobProvider


def _assert_normalized(result: dict, source: str) -> None:
    """Common assertions for all normalized job dicts."""
    assert result["source"] == source
    assert result["hash"]  # non-empty string
    assert len(result["hash"]) == 32  # MD5 hex
    assert result["title"]
    assert result["url"]
    assert isinstance(result["tags"], list)
    assert len(result["tags"]) <= 15
    assert isinstance(result["remote"], bool)
    # Optional fields should be present as keys
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
# Jobicy
# ---------------------------------------------------------------------------


class TestJobicyProvider:
    def test_source_name(self):
        assert JobicyProvider().get_source_name() == "jobicy"

    def test_normalize_job(self):
        raw = {
            "id": 123,
            "jobTitle": "Python Developer",
            "companyName": "ACME Corp",
            "jobDescription": "<p>Build APIs with FastAPI</p>",
            "country": "Switzerland",
            "jobGeo": "Europe",
            "url": "https://jobicy.com/job/123",
            "pubDate": "2026-02-20",
            "jobType": "Full-Time",
            "jobIndustry": "tech",
        }
        result = JobicyProvider().normalize_job(raw)
        _assert_normalized(result, "jobicy")
        assert result["title"] == "Python Developer"
        assert result["company"] == "ACME Corp"
        assert result["remote"] is True

    def test_normalize_missing_fields(self):
        raw = {"jobTitle": "Dev", "companyName": "", "url": "https://x.com/1"}
        result = JobicyProvider().normalize_job(raw)
        _assert_normalized(result, "jobicy")


# ---------------------------------------------------------------------------
# Remotive
# ---------------------------------------------------------------------------


class TestRemotiveProvider:
    def test_source_name(self):
        assert RemotiveProvider().get_source_name() == "remotive"

    def test_normalize_job(self):
        raw = {
            "id": 456,
            "title": "React Engineer",
            "company_name": "StartupX",
            "candidate_required_location": "Europe",
            "tags": ["react", "typescript"],
            "job_type": "full_time",
            "url": "https://remotive.com/job/456",
            "publication_date": "2026-02-20",
            "description": "<p>React and TypeScript</p>",
            "category": "Software Development",
        }
        result = RemotiveProvider().normalize_job(raw)
        _assert_normalized(result, "remotive")
        assert result["title"] == "React Engineer"
        assert result["remote"] is True

    def test_normalize_missing_fields(self):
        raw = {"title": "Dev", "company_name": "", "url": "https://x.com/2"}
        result = RemotiveProvider().normalize_job(raw)
        _assert_normalized(result, "remotive")


# ---------------------------------------------------------------------------
# Arbeitnow
# ---------------------------------------------------------------------------


class TestArbeitnowProvider:
    def test_source_name(self):
        assert ArbeitnowProvider().get_source_name() == "arbeitnow"

    def test_normalize_job(self):
        raw = {
            "slug": "python-dev-123",
            "title": "Python Dev",
            "company_name": "TechCo",
            "location": "Berlin, Germany",
            "remote": True,
            "tags": ["python", "django"],
            "job_types": ["Full-Time"],
            "url": "https://arbeitnow.com/job/123",
            "created_at": 1708387200,
            "description": "We need a Python dev",
        }
        result = ArbeitnowProvider().normalize_job(raw)
        _assert_normalized(result, "arbeitnow")
        assert result["remote"] is True

    def test_normalize_missing_fields(self):
        raw = {"title": "Dev", "company_name": "", "url": "https://x.com/3"}
        result = ArbeitnowProvider().normalize_job(raw)
        _assert_normalized(result, "arbeitnow")


# ---------------------------------------------------------------------------
# JSearch
# ---------------------------------------------------------------------------


class TestJSearchProvider:
    def test_source_name(self):
        assert JSearchProvider().get_source_name() == "jsearch"

    def test_normalize_job(self):
        raw = {
            "job_id": "abc123",
            "job_title": "Backend Engineer",
            "employer_name": "BigCorp",
            "employer_logo": "https://logo.com/img.png",
            "job_city": "Zurich",
            "job_state": "ZH",
            "job_country": "CH",
            "job_is_remote": False,
            "job_employment_type": "FULLTIME",
            "job_apply_link": "https://bigcorp.com/apply/123",
            "job_posted_at_datetime_utc": "2026-02-20T10:00:00Z",
            "job_description": "Build microservices",
            "job_min_salary": 100000,
            "job_max_salary": 130000,
            "job_salary_currency": "CHF",
            "job_salary_period": "YEAR",
        }
        result = JSearchProvider().normalize_job(raw)
        _assert_normalized(result, "jsearch")
        assert result["title"] == "Backend Engineer"
        assert result["logo"] == "https://logo.com/img.png"
        assert "Zurich" in result["location"]

    def test_normalize_missing_fields(self):
        raw = {
            "job_title": "Dev",
            "employer_name": "",
            "job_apply_link": "https://x.com/4",
        }
        result = JSearchProvider().normalize_job(raw)
        _assert_normalized(result, "jsearch")


# ---------------------------------------------------------------------------
# RemoteOK
# ---------------------------------------------------------------------------


class TestRemoteOKProvider:
    def test_source_name(self):
        assert RemoteOKProvider().get_source_name() == "remoteok"

    def test_normalize_job(self):
        raw = {
            "id": "789",
            "position": "DevOps Engineer",
            "company": "CloudCo",
            "location": "Remote",
            "tags": ["devops", "aws", "kubernetes"],
            "date": "2026-02-20",
            "url": "https://remoteok.com/jobs/789",
            "apply_url": "https://cloudco.com/apply",
            "description": "<p>Manage cloud infrastructure</p>",
            "logo": "https://remoteok.com/logo.png",
            "slug": "devops-cloudco",
            "salary_min": 120,
            "salary_max": 180,
        }
        result = RemoteOKProvider().normalize_job(raw)
        _assert_normalized(result, "remoteok")
        assert result["remote"] is True
        # Salary should be multiplied by 1000
        assert result.get("salary_original") is not None

    def test_normalize_missing_fields(self):
        raw = {"position": "Dev", "company": "", "url": "https://x.com/5"}
        result = RemoteOKProvider().normalize_job(raw)
        _assert_normalized(result, "remoteok")


# ---------------------------------------------------------------------------
# Himalayas
# ---------------------------------------------------------------------------


class TestHimalayasProvider:
    def test_source_name(self):
        assert HimalayasProvider().get_source_name() == "himalayas"

    def test_normalize_job(self):
        raw = {
            "guid": "him-123",
            "title": "Full Stack Developer",
            "companyName": "MountainTech",
            "locationRestrictions": ["Europe"],
            "categories": ["engineering"],
            "applicationLink": "https://himalayas.app/job/123",
            "pubDate": "2026-02-20",
            "excerpt": "Join our team",
            "companyLogo": "https://himalayas.app/logo.png",
            "minSalary": 80000,
            "maxSalary": 120000,
            "currency": "USD",
            "employmentType": "Full-Time",
        }
        result = HimalayasProvider().normalize_job(raw)
        _assert_normalized(result, "himalayas")
        assert result["title"] == "Full Stack Developer"

    def test_normalize_missing_fields(self):
        raw = {"title": "Dev", "companyName": "", "applicationLink": "https://x.com/6"}
        result = HimalayasProvider().normalize_job(raw)
        _assert_normalized(result, "himalayas")


# ---------------------------------------------------------------------------
# Adzuna
# ---------------------------------------------------------------------------


class TestAdzunaProvider:
    def test_source_name(self):
        assert AdzunaProvider().get_source_name() == "adzuna"

    def test_normalize_job(self):
        raw = {
            "id": "adzuna-123",
            "title": "<b>Senior Python Developer</b>",
            "company": {"display_name": "FinTech AG"},
            "location": {"display_name": "Zurich", "area": ["Switzerland"]},
            "redirect_url": "https://adzuna.com/job/123",
            "created": "2026-02-20T10:00:00Z",
            "description": "Python and FastAPI",
            "category": {"label": "IT Jobs"},
            "salary_min": 90000,
            "salary_max": 120000,
            "contract_type": "permanent",
            "contract_time": "full_time",
        }
        result = AdzunaProvider().normalize_job(raw)
        _assert_normalized(result, "adzuna")
        assert "Senior Python Developer" in result["title"]
        assert "<b>" not in result["title"]

    def test_normalize_missing_fields(self):
        raw = {"title": "Dev", "redirect_url": "https://x.com/7"}
        result = AdzunaProvider().normalize_job(raw)
        _assert_normalized(result, "adzuna")


# ---------------------------------------------------------------------------
# WeWorkRemotely
# ---------------------------------------------------------------------------


class TestWeWorkRemotelyProvider:
    def test_source_name(self):
        assert WeWorkRemotelyProvider().get_source_name() == "weworkremotely"

    def test_normalize_job(self):
        item = ET.Element("item")
        ET.SubElement(item, "title").text = "ACME Corp: Python Developer"
        ET.SubElement(item, "link").text = "https://weworkremotely.com/job/123"
        ET.SubElement(item, "description").text = "<p>Build Python APIs</p>"
        ET.SubElement(item, "region").text = "Europe"
        ET.SubElement(item, "pubDate").text = "2026-02-20"
        result = WeWorkRemotelyProvider().normalize_job(item)
        _assert_normalized(result, "weworkremotely")
        assert result["remote"] is True
        # Should split "ACME Corp: Python Developer" into company and title
        assert result["company"] == "ACME Corp"
        assert result["title"] == "Python Developer"

    def test_normalize_no_colon_in_title(self):
        item = ET.Element("item")
        ET.SubElement(item, "title").text = "Just A Title"
        ET.SubElement(item, "link").text = "https://weworkremotely.com/job/456"
        result = WeWorkRemotelyProvider().normalize_job(item)
        _assert_normalized(result, "weworkremotely")
        assert result["title"] == "Just A Title"

    def test_normalize_missing_fields(self):
        item = ET.Element("item")
        ET.SubElement(item, "title").text = "Dev"
        ET.SubElement(item, "link").text = "https://x.com/8"
        result = WeWorkRemotelyProvider().normalize_job(item)
        _assert_normalized(result, "weworkremotely")


# ---------------------------------------------------------------------------
# Ostjob
# ---------------------------------------------------------------------------


class TestOstjobProvider:
    def test_source_name(self):
        assert OstjobProvider().get_source_name() == "ostjob"


# ---------------------------------------------------------------------------
# Zentraljob
# ---------------------------------------------------------------------------


class TestZentraljobProvider:
    def test_source_name(self):
        assert ZentraljobProvider().get_source_name() == "zentraljob"


# ---------------------------------------------------------------------------
# SwissTechJobs
# ---------------------------------------------------------------------------


class TestSwissTechJobsProvider:
    def test_source_name(self):
        assert SwissTechJobsProvider().get_source_name() == "swisstechjobs"

    def test_normalize_job(self):
        raw = {
            "id": 100,
            "title": {"rendered": "<b>Scala Engineer</b>"},
            "content": {"rendered": "<p>Work with Scala and Akka</p>"},
            "link": "https://swisstechjobs.com/job/100",
            "date": "2026-02-20T10:00:00",
            "meta": {
                "_company_name": "SwissTech",
                "_job_location": "Zurich",
                "_remote_position": True,
                "_job_salary": "120000",
                "_job_salary_currency": "CHF",
            },
        }
        result = SwissTechJobsProvider().normalize_job(raw)
        _assert_normalized(result, "swisstechjobs")
        assert "Scala Engineer" in result["title"]
        assert "<b>" not in result["title"]

    def test_normalize_missing_fields(self):
        raw = {
            "title": {"rendered": "Dev"},
            "link": "https://x.com/9",
        }
        result = SwissTechJobsProvider().normalize_job(raw)
        _assert_normalized(result, "swisstechjobs")


# ---------------------------------------------------------------------------
# ICTjobs
# ---------------------------------------------------------------------------


class TestICTJobsProvider:
    def test_source_name(self):
        assert ICTJobsProvider().get_source_name() == "ictjobs"

    def test_normalize_job(self):
        raw = {
            "id": 200,
            "title": {"rendered": "<b>Java Developer</b>"},
            "link": "https://ictjobs.ch/job/200",
            "date": "2026-02-20T10:00:00",
            "acf": {
                "intro": "Join our team",
                "description": "Work with Java and Spring",
                "location": "Bern",
                "has_home_office": False,
                "direct_link": "",
                "use_direct_link": False,
                "salary_min": 90000,
                "salary_max": 110000,
            },
            "_embedded": {
                "wp:term": [
                    [
                        {"name": "java", "taxonomy": "post_tag"},
                        {"name": "spring", "taxonomy": "post_tag"},
                    ],
                    [
                        {"name": "Bern", "taxonomy": "ctx_work_location"},
                    ],
                ]
            },
        }
        result = ICTJobsProvider().normalize_job(raw)
        _assert_normalized(result, "ictjobs")
        assert "Java Developer" in result["title"]

    def test_normalize_missing_fields(self):
        raw = {
            "title": {"rendered": "Dev"},
            "link": "https://x.com/10",
        }
        result = ICTJobsProvider().normalize_job(raw)
        _assert_normalized(result, "ictjobs")


# ---------------------------------------------------------------------------
# Jooble
# ---------------------------------------------------------------------------


class TestJoobleProvider:
    def test_source_name(self):
        assert JoobleProvider().get_source_name() == "jooble"

    def test_normalize_job(self):
        raw = {
            "title": "Data Engineer",
            "company": "DataCo",
            "location": "Geneva, Switzerland",
            "snippet": "Process big data pipelines",
            "salary": "CHF 120,000",
            "type": "Full-Time",
            "link": "https://jooble.org/job/123",
            "updated": "2026-02-20",
            "id": "jooble-123",
        }
        result = JoobleProvider().normalize_job(raw)
        _assert_normalized(result, "jooble")
        assert result["title"] == "Data Engineer"
        assert result["company"] == "DataCo"
        assert result["canton"] == "GE"  # Geneva → GE

    def test_normalize_missing_fields(self):
        raw = {"title": "Dev", "company": "", "link": "https://x.com/11"}
        result = JoobleProvider().normalize_job(raw)
        _assert_normalized(result, "jooble")


# ---------------------------------------------------------------------------
# Careerjet
# ---------------------------------------------------------------------------


class TestCareerjetProvider:
    def test_source_name(self):
        assert CareerjetProvider().get_source_name() == "careerjet"

    def test_normalize_job(self):
        raw = {
            "title": "QA Engineer",
            "company": "QualityCo",
            "locations": "Zurich, Switzerland",
            "url": "https://careerjet.ch/job/123",
            "date": "2026-02-20",
            "description": "<p>Test all the things</p>",
            "salary": "CHF 95,000 - 110,000",
            "site": "careerjet.ch",
        }
        result = CareerjetProvider().normalize_job(raw)
        _assert_normalized(result, "careerjet")
        assert result["title"] == "QA Engineer"
        assert result["canton"] == "ZH"

    def test_normalize_missing_fields(self):
        raw = {"title": "Dev", "company": "", "url": "https://x.com/12"}
        result = CareerjetProvider().normalize_job(raw)
        _assert_normalized(result, "careerjet")


# ---------------------------------------------------------------------------
# zebis.ch (RSS)
# ---------------------------------------------------------------------------

DC_NS = "http://purl.org/dc/elements/1.1/"

# Feed REAL recortado (sonda 2026-08-14, VD.9): el portal emite link/guid con
# su base mal configurada (https://0.0.0.0:3000/stellen/<slug>).
_ZEBIS_FIXTURE = Path(__file__).parent / "fixtures" / "zebis_feed.xml"


def _zebis_fixture_items() -> list[ET.Element]:
    root = ET.parse(_ZEBIS_FIXTURE).getroot()
    return root.find("channel").findall("item")


class TestZebisProvider:
    def test_source_name(self):
        assert ZebisProvider().get_source_name() == "zebis"

    def test_canonical_url_from_broken_feed_base(self):
        """El host del feed (0.0.0.0:3000) NUNCA se emite: la URL se
        reconstituye sobre www.zebis.ch a partir del path del link real."""
        provider = ZebisProvider()
        results = provider._process_raw_jobs(_zebis_fixture_items())
        assert len(results) == 5  # ningún item real se pierde
        for job in results:
            assert "0.0.0.0" not in job["url"]
            assert job["url"].startswith("https://www.zebis.ch/stellen/")
        assert results[0]["url"] == (
            "https://www.zebis.ch/stellen/"
            "fachlehrperson-daz-kindergartenstufe-3-lektionen-0"
        )

    def test_pubdate_to_published_at_aware(self):
        # pubDate RFC822 del feed real → published_at timezone-aware en UTC.
        result = ZebisProvider().normalize_job(_zebis_fixture_items()[0])
        assert result["published_at"] == datetime(
            2026, 8, 13, 13, 16, 35, tzinfo=timezone.utc
        )

    @pytest.mark.parametrize(
        "raw_url",
        [
            # Diferencial urllib/WHATWG: el navegador corta el host en "\".
            "https://evil.com\\@www.zebis.ch/stellen/x",
            # Protocolo-relativo: el "host" ajeno cae en netloc.
            "//evil.com/stellen/x",
            # Esquema no navegable.
            "javascript:alert(1)",
            # Userinfo: spoofing visual del enlace.
            "https://user@0.0.0.0:3000/stellen/x",
            # Path ajeno al listado de ofertas.
            "https://0.0.0.0:3000/otra-cosa/x",
            # Path con segmentos extra.
            "https://0.0.0.0:3000/stellen/a/b",
            # Portada, sin oferta propia.
            "https://0.0.0.0:3000/",
            "",
        ],
    )
    def test_canonical_url_rejects_unsafe(self, raw_url):
        from providers.zebis import _canonical_job_url

        assert _canonical_job_url(raw_url) is None

    def test_canonical_url_invalid_ipv6_falls_back_to_guid(self):
        """VD.9/H4: un link con IPv6 inválido ("https://[evil/...") hacía que
        urlsplit propagara ValueError en vez de devolver None — la excepción
        escapaba del parseo y una oferta legítima con guid BUENO se perdía
        porque el fallback al guid nunca llegaba a ejecutarse."""
        from providers.zebis import _canonical_job_url

        assert _canonical_job_url("https://[evil/stellen/x") is None

        # Oferta real: link roto pero guid utilizable ⇒ la oferta se emite.
        item = ET.Element("item")
        ET.SubElement(item, "title").text = "Lehrperson"
        ET.SubElement(item, "link").text = "https://[evil/stellen/x"
        ET.SubElement(item, "guid").text = "https://0.0.0.0:3000/stellen/lehrperson-1"
        result = ZebisProvider().normalize_job(item)
        assert result["url"] == "https://www.zebis.ch/stellen/lehrperson-1"

    def test_canonical_url_rejects_control_chars(self):
        """VD.9/H5 (+V2-4): caracteres de control (< 0x20 y DEL 0x7f) en el
        slug no deben sobrevivir hasta una URL persistida (calidad de dato —
        el host es siempre nuestro, no hay riesgo de host). El espacio se
        deja pasar a propósito: no es de control. Residual documentado: un
        "%00" LITERAL (tres chars imprimibles) pasa el filtro."""
        from providers.zebis import _canonical_job_url

        # \t\r\n no se prueban: urlsplit los ELIMINA antes de parsear
        # (unsafe URL bytes) y nunca llegan al regex del path.
        assert _canonical_job_url("https://0.0.0.0:3000/stellen/x\x01") is None
        assert _canonical_job_url("https://0.0.0.0:3000/stellen/x\x1f") is None
        # V2-4: DEL (0x7f) también se rechaza — antes atravesaba el filtro.
        assert _canonical_job_url("https://0.0.0.0:3000/stellen/x\x7f") is None
        # El espacio sobrevive (documentado): no es carácter de control.
        assert (
            _canonical_job_url("https://0.0.0.0:3000/stellen/a b")
            == "https://www.zebis.ch/stellen/a b"
        )

    def test_stelle_admin_resolve_url_invalid_ipv6_returns_none(self):
        """VD.9/H4 (defecto gemelo): `_resolve_job_url()` de stelle_admin
        compartía el patrón — urljoin/urlsplit lanzan ValueError con un href
        IPv6 inválido y tumbaban el lote del scraper. Vive aquí (y no en
        test_scrapers.py) porque ese fichero lo están tocando otros agentes
        durante VD.9."""
        from scrapers.stelle_admin import _resolve_job_url

        assert _resolve_job_url("https://[evil/job/x") is None

    def test_workload_lookbehind_ignores_years(self):
        """VD.9/H6: cubre el lookbehind (?<!\\d) anti-años del regex de
        pensum — la mutación de quitarlo dejaba los 21 tests en verde.
        Primer título: REAL del feed (2026-08-15), con "Schuljahr 2026/27".
        Segundo: composición mínima de dos formas reales del feed (año con
        guión espaciado, como en "… ab Mitte Oktober 2026 - bis …", + pensum
        "100 %") que discrimina la mutación: sin el lookbehind el regex
        muerde dentro del año y extrae "026 - 100 %"."""
        real_title = (
            "Zwei Klassenlehrpersonen gesucht - für unsere 5. Klasse und "
            "unsere 6. Klasse, Schuljahr 2026/27 für 24-28 Lektionen"
        )
        cases = [
            (real_title, None),  # el año nunca produce un pensum
            ("Stellvertretung ab Oktober 2026 - 100 %", "100 %"),
        ]
        for title, expected in cases:
            item = ET.Element("item")
            ET.SubElement(item, "title").text = title
            ET.SubElement(item, "link").text = "https://www.zebis.ch/stellen/x"
            result = ZebisProvider().normalize_job(item)
            assert result["employment_type"] == expected, title

    def test_item_without_usable_url_is_dropped(self):
        # Ni link ni guid utilizables ⇒ url vacía ⇒ _process_raw_jobs
        # descarta el item ("title and url must be non-empty").
        item = ET.Element("item")
        ET.SubElement(item, "title").text = "Lehrperson"
        ET.SubElement(item, "link").text = "javascript:alert(1)"
        ET.SubElement(item, "guid").text = "//evil.com/stellen/x"
        provider = ZebisProvider()
        assert provider.normalize_job(item)["url"] == ""
        assert provider._process_raw_jobs([item]) == []

    @pytest.mark.parametrize(
        ("index", "expected"),
        [
            (0, None),  # "(3 Lektionen)" no es un pensum
            (1, "46%"),  # "(13 Lektionen / 46%)"
            (2, "80 – 100 %"),  # sin paréntesis, con guión largo
            (3, "90 %"),  # "(90 %)" — la forma que ya cubría el regex viejo
            (4, "80 %"),  # ", 80 %" al final del título
        ],
    )
    def test_workload_from_real_titles(self, index, expected):
        """Pensum extraído de los títulos REALES del feed (con y sin
        paréntesis) — el regex viejo exigía paréntesis al final y perdía
        la mayoría de las formas reales."""
        result = ZebisProvider().normalize_job(_zebis_fixture_items()[index])
        assert result["employment_type"] == expected

    def test_normalize_job(self):
        item = ET.Element("item")
        ET.SubElement(item, "title").text = "Klassenlehrperson (80 %)"
        ET.SubElement(
            item, "link"
        ).text = "https://www.zebis.ch/stellen/klassenlehrperson-80"
        ET.SubElement(item, "description").text = (
            "<p><strong>Schule Kilchberg</strong></p>"
            "<p>Kilchberg ist eine attraktive Gemeinde am Zürichsee.</p>"
        )
        ET.SubElement(item, "pubDate").text = "27.2.2026"
        dc_creator = ET.SubElement(item, f"{{{DC_NS}}}creator")
        dc_creator.text = "kundendienst_14364"
        ET.SubElement(
            item, "guid"
        ).text = "https://www.zebis.ch/stellen/klassenlehrperson-80"
        result = ZebisProvider().normalize_job(item)
        _assert_normalized(result, "zebis")
        assert result["title"] == "Klassenlehrperson (80 %)"
        assert result["company"] == "Schule Kilchberg"
        assert result["remote"] is False
        assert result["employment_type"] == "80 %"

    def test_normalize_job_no_employer_in_description(self):
        item = ET.Element("item")
        ET.SubElement(item, "title").text = "Lehrperson Sek I"
        ET.SubElement(item, "link").text = "https://www.zebis.ch/stellen/lehrperson"
        ET.SubElement(
            item, "description"
        ).text = "<p>Wir suchen eine engagierte Lehrperson.</p>"
        result = ZebisProvider().normalize_job(item)
        _assert_normalized(result, "zebis")
        assert result["company"] == "Unknown"

    def test_normalize_job_minimal(self):
        item = ET.Element("item")
        ET.SubElement(item, "title").text = "Lehrperson"
        ET.SubElement(item, "link").text = "https://www.zebis.ch/stellen/lp"
        result = ZebisProvider().normalize_job(item)
        _assert_normalized(result, "zebis")

    async def test_rss_ilegible_registra_fetch_issue(self, monkeypatch):
        """Fase 3/H2 rama 1: un 200 cuyo XML no parsea (ET.ParseError) salía
        como `empty` con 0 issues (G1 rota) — solo se logeaba. Debe
        registrarse como fallo de fetch para que classify dé `error`."""
        import providers.zebis as zebis_module
        from utils import fetch_diagnostics as diag

        async def fake_rss(client, url, **kwargs):
            return "<rss><channel><item>truncado"  # XML inválido

        monkeypatch.setattr(zebis_module, "fetch_rss", fake_rss)
        diag.begin()
        assert await ZebisProvider().fetch_jobs("") == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "ilegible" in issues[0].detail

    async def test_rss_sin_channel_registra_fetch_issue(self, monkeypatch):
        """Fase 3/H2 rama 2: XML válido pero sin <channel> es estructura
        desconocida (no es el RSS del portal), nunca un feed vacío."""
        import providers.zebis as zebis_module
        from utils import fetch_diagnostics as diag

        async def fake_rss(client, url, **kwargs):
            return "<html><body>Not an RSS feed</body></html>"

        monkeypatch.setattr(zebis_module, "fetch_rss", fake_rss)
        diag.begin()
        assert await ZebisProvider().fetch_jobs("") == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "channel" in issues[0].detail

    async def test_channel_sin_items_es_vacio_legitimo(self, monkeypatch):
        """No-regresión G2: un <channel> válido con cero <item> es vacío
        legítimo (0 issues) — la vía de rehabilitación de un board sin
        vacantes no debe convertirse en `error`."""
        import providers.zebis as zebis_module
        from utils import fetch_diagnostics as diag

        async def fake_rss(client, url, **kwargs):
            return (
                '<?xml version="1.0"?><rss version="2.0"><channel>'
                "<title>Stellen</title></channel></rss>"
            )

        monkeypatch.setattr(zebis_module, "fetch_rss", fake_rss)
        diag.begin()
        assert await ZebisProvider().fetch_jobs("") == []
        assert diag.issues() == []

    async def test_cuerpo_vacio_registra_fetch_issue(self, monkeypatch):
        """Fase 3/H2: un 200 con cuerpo VACÍO ("") salía como `empty` con 0
        issues — `if not xml_text` lo confundía con el None de fetch_rss
        (fetch fallido cuyo issue ya registró utils.http). El "" debe fluir
        a ET.fromstring para que el ParseError lo haga visible."""
        import providers.zebis as zebis_module
        from utils import fetch_diagnostics as diag

        async def fake_rss(client, url, **kwargs):
            return ""  # 200 con body vacío: fetch "exitoso" sin contenido

        monkeypatch.setattr(zebis_module, "fetch_rss", fake_rss)
        diag.begin()
        assert await ZebisProvider().fetch_jobs("") == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "ilegible" in issues[0].detail

    async def test_fetch_fallido_none_corta_sin_issue_propio(self, monkeypatch):
        """Fija la decisión (NO discrimina un bug: pasaba también antes del
        arreglo de H2): el None de fetch_rss corta sin registrar issue
        PROPIO — el fallo de red/HTTP ya lo registró utils.http y duplicarlo
        inflaría el recuento de issues del run."""
        import providers.zebis as zebis_module
        from utils import fetch_diagnostics as diag

        async def fake_rss(client, url, **kwargs):
            return None  # fetch fallido: utils.http ya registró su issue

        monkeypatch.setattr(zebis_module, "fetch_rss", fake_rss)
        diag.begin()
        assert await ZebisProvider().fetch_jobs("") == []
        assert diag.issues() == []

    async def test_items_y_ninguno_normalizable_registra_fetch_issue(self, monkeypatch):
        """Fase 3 r2/H2: feed bien formado cuyos <item> pierden TODOS la URL
        utilizable (p. ej. el portal migra el path de /stellen/<slug> a
        /jobs/<slug> — y este portal YA demostró tener la base del feed mal
        configurada): todos caían en los descartes de _process_raw_jobs y el
        run salía `empty` con 0 issues. Misma regla que thehub/financejobs
        ("N elementos y ninguno parseable")."""
        import providers.zebis as zebis_module
        from utils import fetch_diagnostics as diag

        items = "".join(
            f"<item><title>Lehrperson {i}</title>"
            f"<link>https://www.zebis.ch/jobs/lehrperson-{i}</link></item>"
            for i in range(3)
        )
        feed = (
            f'<?xml version="1.0"?><rss version="2.0"><channel>{items}</channel></rss>'
        )

        async def fake_rss(client, url, **kwargs):
            return feed

        monkeypatch.setattr(zebis_module, "fetch_rss", fake_rss)
        diag.begin()
        assert await ZebisProvider().fetch_jobs("") == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "3 items y ninguno es normalizable" in issues[0].detail

    async def test_query_que_filtra_todo_es_vacio_legitimo(self, monkeypatch):
        """No-regresión G2 (Fase 3 r2/H2): el guard va ANTES del filtro por
        query — un query que descarta todos los resultados normalizados es
        vacío legítimo, SIN issue (la vía de rehabilitación no se rompe)."""
        import providers.zebis as zebis_module
        from utils import fetch_diagnostics as diag

        feed = (
            '<?xml version="1.0"?><rss version="2.0"><channel>'
            "<item><title>Klassenlehrperson (80 %)</title>"
            "<link>https://www.zebis.ch/stellen/klassenlehrperson-80</link>"
            "</item></channel></rss>"
        )

        async def fake_rss(client, url, **kwargs):
            return feed

        monkeypatch.setattr(zebis_module, "fetch_rss", fake_rss)
        diag.begin()
        assert await ZebisProvider().fetch_jobs("zzz-sin-coincidencia") == []
        assert diag.issues() == []

    def test_normalize_job_title_with_percentage(self):
        item = ET.Element("item")
        ET.SubElement(
            item, "title"
        ).text = "Fachlehrperson im gestalterischen Bereich, Pensum 50 %"
        ET.SubElement(item, "link").text = "https://www.zebis.ch/stellen/fach"
        ET.SubElement(
            item, "description"
        ).text = "<p><strong>Schule Giswil</strong></p><p>Description here.</p>"
        result = ZebisProvider().normalize_job(item)
        _assert_normalized(result, "zebis")
        assert result["company"] == "Schule Giswil"
        # Pensum sin paréntesis también se extrae (formas reales del feed,
        # VD.9 — antes exigía "(...%)" al final y salía None).
        assert result["employment_type"] == "50 %"


# ---------------------------------------------------------------------------
# publicjobs.ch (SvelteKit JSON)
# ---------------------------------------------------------------------------


class TestPublicJobsProvider:
    def test_source_name(self):
        assert PublicJobsProvider().get_source_name() == "publicjobs"

    def test_normalize_job(self):
        raw = {
            "title": "Primarlehrer/in",
            "company": "Stadt Bern",
            "url": "https://www.publicjobs.ch/jobs/primarlehrer-bern",
            "location": "Bern",
            "canton": "BE",
            "description": "Wir suchen eine Primarlehrperson für die 3. Klasse.",
            "employment_type": "80% - 100%",
            "logo": "https://www.publicjobs.ch/logos/bern.png",
        }
        result = PublicJobsProvider().normalize_job(raw)
        _assert_normalized(result, "publicjobs")
        assert result["title"] == "Primarlehrer/in"
        assert result["company"] == "Stadt Bern"
        assert result["canton"] == "BE"
        assert result["employment_type"] == "80% - 100%"

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Lehrer",
            "company": "",
            "url": "https://www.publicjobs.ch/jobs/lehrer",
        }
        result = PublicJobsProvider().normalize_job(raw)
        _assert_normalized(result, "publicjobs")
