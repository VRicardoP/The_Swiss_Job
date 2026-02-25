"""Tests for all 14 provider normalize_job methods."""

import xml.etree.ElementTree as ET

from providers.adzuna import AdzunaProvider
from providers.arbeitnow import ArbeitnowProvider
from providers.careerjet import CareerjetProvider
from providers.himalayas import HimalayasProvider
from providers.ictjobs import ICTJobsProvider
from providers.jobicy import JobicyProvider
from providers.jooble import JoobleProvider
from providers.jsearch import JSearchProvider
from providers.ostjob import OstjobProvider
from providers.remoteok import RemoteOKProvider
from providers.remotive import RemotiveProvider
from providers.swisstechjobs import SwissTechJobsProvider
from providers.weworkremotely import WeWorkRemotelyProvider
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
