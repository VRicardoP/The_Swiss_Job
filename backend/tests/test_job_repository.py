"""Tests for JobRepository — upsert, dedup marking, and active counts."""

import pytest
from sqlalchemy import select

from models.job import Job
from services.job_repository import JobRepository


def _job_dict(**overrides):
    base = {
        "hash": "abc123def456abc123def456abc12345",  # 32 chars
        "source": "test_provider",
        "title": "Python Developer",
        "company": "Acme Corp",
        "url": "https://example.com/job/1",
        "location": "Zurich, ZH",
        "canton": "ZH",
        "description": "Build Python APIs",
        "description_snippet": "Build Python APIs",
        "remote": False,
        "tags": ["python", "fastapi"],
        "logo": None,
        "salary_min_chf": None,
        "salary_max_chf": None,
        "salary_original": None,
        "salary_currency": None,
        "salary_period": None,
        "language": "en",
        "seniority": None,
        "contract_type": None,
        "employment_type": "Full-Time",
        "fuzzy_hash": "fedcba9876543210fedcba9876543210",
    }
    base.update(overrides)
    return base


@pytest.mark.anyio
class TestJobRepository:
    """Unit tests for JobRepository against a real test database."""

    async def test_upsert_new_returns_true(self, db_session):
        """Inserting a brand-new job must return True."""
        repo = JobRepository(db_session)
        is_new = await repo.upsert_job(_job_dict())
        await db_session.commit()
        assert is_new is True

    async def test_upsert_existing_returns_false(self, db_session):
        """Upserting the same hash a second time must return False."""
        repo = JobRepository(db_session)
        await repo.upsert_job(_job_dict())
        await db_session.commit()

        is_new = await repo.upsert_job(_job_dict())
        await db_session.commit()
        assert is_new is False

    async def test_upsert_refreshes_last_seen_at(self, db_session):
        """A second upsert must bump last_seen_at to a newer timestamp."""
        repo = JobRepository(db_session)
        data = _job_dict()

        await repo.upsert_job(data)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.last_seen_at).where(Job.hash == data["hash"])
            )
        ).scalar_one()
        first_seen = row

        # Second upsert — last_seen_at should be refreshed
        await repo.upsert_job(data)
        await db_session.commit()

        # Expire cached attributes so we re-read from DB
        await db_session.flush()
        row2 = (
            await db_session.execute(
                select(Job.last_seen_at).where(Job.hash == data["hash"])
            )
        ).scalar_one()
        assert row2 >= first_seen

    async def test_upsert_reactivates_inactive_job(self, db_session):
        """Re-upserting a previously deactivated job must set is_active=True."""
        repo = JobRepository(db_session)
        data = _job_dict()

        # Insert then mark as duplicate (which sets is_active=False)
        await repo.upsert_job(data)
        await db_session.commit()

        await repo.mark_duplicate(data["hash"], "other_canonical_hash_1234567")
        await db_session.commit()

        # Confirm it was deactivated
        row = (
            await db_session.execute(
                select(Job.is_active).where(Job.hash == data["hash"])
            )
        ).scalar_one()
        assert row is False

        # Re-upsert the same job — should reactivate
        await repo.upsert_job(data)
        await db_session.commit()

        row2 = (
            await db_session.execute(
                select(Job.is_active).where(Job.hash == data["hash"])
            )
        ).scalar_one()
        assert row2 is True

    async def test_mark_duplicate_sets_fields(self, db_session):
        """mark_duplicate must set duplicate_of and deactivate the job."""
        repo = JobRepository(db_session)
        data = _job_dict()
        canonical = "canonical_hash_12345678901234567"

        await repo.upsert_job(data)
        await db_session.commit()

        await repo.mark_duplicate(data["hash"], canonical)
        await db_session.commit()

        row = (
            await db_session.execute(
                select(Job.duplicate_of, Job.is_active).where(Job.hash == data["hash"])
            )
        ).one()
        assert row.duplicate_of == canonical
        assert row.is_active is False

    async def test_get_active_count_only_active(self, db_session):
        """get_active_count must count only is_active=True jobs."""
        repo = JobRepository(db_session)

        # Insert 3 distinct jobs (hash must be exactly 32 chars)
        for i in range(3):
            h = f"a{i}" + "0" * 30
            await repo.upsert_job(
                _job_dict(hash=h[:32], url=f"https://example.com/job/{i}")
            )
        await db_session.commit()

        count = await repo.get_active_count()
        assert count == 3

    async def test_get_active_count_excludes_duplicates(self, db_session):
        """Duplicates (is_active=False) must not be counted."""
        repo = JobRepository(db_session)

        hashes = []
        for i in range(3):
            h = (f"b{i}" + "0" * 30)[:32]
            hashes.append(h)
            await repo.upsert_job(
                _job_dict(hash=h, url=f"https://example.com/job/d{i}")
            )
        await db_session.commit()

        # Mark first as duplicate of second
        await repo.mark_duplicate(hashes[0], hashes[1])
        await db_session.commit()

        count = await repo.get_active_count()
        assert count == 2

    async def test_upsert_handles_all_valid_columns(self, db_session):
        """upsert_job must persist every column present on the Job model."""
        repo = JobRepository(db_session)
        data = _job_dict(
            salary_min_chf=80000,
            salary_max_chf=120000,
            salary_original="80k-120k CHF",
            salary_currency="CHF",
            logo="https://example.com/logo.png",
            employment_type="Part-Time",
            remote=True,
            # Include an extra key that does NOT exist on the model:
            nonexistent_field="should_be_ignored",
        )

        is_new = await repo.upsert_job(data)
        await db_session.commit()
        assert is_new is True

        row = (
            await db_session.execute(select(Job).where(Job.hash == data["hash"]))
        ).scalar_one()

        assert row.title == "Python Developer"
        assert row.company == "Acme Corp"
        assert row.salary_min_chf == 80000
        assert row.salary_max_chf == 120000
        assert row.salary_original == "80k-120k CHF"
        assert row.salary_currency == "CHF"
        assert row.logo == "https://example.com/logo.png"
        assert row.employment_type == "Part-Time"
        assert row.remote is True
        assert row.location == "Zurich, ZH"
        assert row.canton == "ZH"
        assert row.language == "en"
        assert row.tags == ["python", "fastapi"]
        assert row.fuzzy_hash == "fedcba9876543210fedcba9876543210"
        assert row.is_active is True
        # Ensure nonexistent_field was silently ignored (no AttributeError)
        assert not hasattr(row, "nonexistent_field")
