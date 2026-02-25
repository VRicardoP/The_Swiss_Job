"""Tests for Job model, enums, and schemas."""

import pytest
from sqlalchemy import select

from models.enums import ContractType, SalaryPeriod, Seniority
from models.job import Job
from schemas.job import JobBrief, JobCreate


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnums:
    def test_salary_period_values(self):
        assert SalaryPeriod.yearly.value == "yearly"
        assert SalaryPeriod.monthly.value == "monthly"
        assert SalaryPeriod.hourly.value == "hourly"

    def test_seniority_values(self):
        assert Seniority.intern.value == "intern"
        assert Seniority.junior.value == "junior"
        assert Seniority.mid.value == "mid"
        assert Seniority.senior.value == "senior"
        assert Seniority.lead.value == "lead"
        assert Seniority.director.value == "director"

    def test_contract_type_values(self):
        assert ContractType.full_time.value == "full_time"
        assert ContractType.part_time.value == "part_time"
        assert ContractType.contract.value == "contract"
        assert ContractType.internship.value == "internship"
        assert ContractType.apprenticeship.value == "apprenticeship"
        assert ContractType.temporary.value == "temporary"


# ---------------------------------------------------------------------------
# Job model DB operations
# ---------------------------------------------------------------------------


class TestJobModel:
    @pytest.fixture
    def sample_job_data(self):
        return {
            "hash": "a" * 32,
            "source": "test_provider",
            "title": "Python Developer",
            "company": "ACME Corp",
            "location": "Zurich, Switzerland",
            "canton": "ZH",
            "description": "Build awesome APIs",
            "description_snippet": "Build awesome APIs",
            "url": "https://example.com/job/1",
            "remote": True,
            "tags": ["python", "fastapi", "docker"],
            "is_active": True,
        }

    async def test_create_job(self, db_session, sample_job_data):
        job = Job(**sample_job_data)
        db_session.add(job)
        await db_session.commit()

        result = await db_session.execute(
            select(Job).where(Job.hash == sample_job_data["hash"])
        )
        fetched = result.scalar_one()
        assert fetched.hash == sample_job_data["hash"]
        assert fetched.title == "Python Developer"
        assert fetched.company == "ACME Corp"
        assert fetched.canton == "ZH"
        assert fetched.tags == ["python", "fastapi", "docker"]
        assert fetched.is_active is True
        assert fetched.first_seen_at is not None
        assert fetched.last_seen_at is not None

    async def test_hash_is_pk_32_chars(self, db_session, sample_job_data):
        job = Job(**sample_job_data)
        db_session.add(job)
        await db_session.commit()
        assert len(job.hash) == 32

    async def test_url_unique_constraint(self, db_session, sample_job_data):
        job1 = Job(**sample_job_data)
        db_session.add(job1)
        await db_session.commit()

        # Same URL, different hash
        data2 = {**sample_job_data, "hash": "b" * 32}
        job2 = Job(**data2)
        db_session.add(job2)
        with pytest.raises(Exception):
            await db_session.commit()

    async def test_nullable_fields(self, db_session):
        """Minimal job with only required fields."""
        job = Job(
            hash="c" * 32,
            source="test",
            title="Dev",
            company="Co",
            url="https://example.com/job/minimal",
            remote=False,
            tags=[],
        )
        db_session.add(job)
        await db_session.commit()

        result = await db_session.execute(select(Job).where(Job.hash == "c" * 32))
        fetched = result.scalar_one()
        assert fetched.canton is None
        assert fetched.salary_min_chf is None
        assert fetched.language is None
        assert fetched.seniority is None
        assert fetched.contract_type is None
        assert fetched.embedding is None


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TestJobSchemas:
    def test_job_create(self):
        data = JobCreate(
            hash="a" * 32,
            source="jooble",
            title="Dev",
            company="ACME",
            url="https://example.com/1",
        )
        assert data.hash == "a" * 32
        assert data.remote is False
        assert data.tags == []

    def test_job_brief_from_attributes(self):
        """Test that JobBrief can be constructed from a dict (simulating ORM)."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        brief = JobBrief(
            hash="d" * 32,
            title="QA Engineer",
            company="TestCo",
            url="https://example.com/2",
            source="remotive",
            first_seen_at=now,
            is_active=True,
        )
        assert brief.title == "QA Engineer"
        assert brief.remote is False
        assert brief.tags == []
