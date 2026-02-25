"""Tests for embedding Celery tasks."""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import numpy as np

from models.job import Job
from models.user import User
from models.user_profile import UserProfile
from tasks.embedding_tasks import (
    _generate_job_embeddings_async,
    _generate_profile_embedding_async,
)


async def _create_user_with_profile(db, cv_text=None, title=None, skills=None):
    """Create a test user with profile and return user_id."""
    from core.security import hash_password

    user_id = uuid.uuid4()
    user = User(
        id=user_id,
        email=f"test-{user_id.hex[:8]}@example.com",
        hashed_password=hash_password("TestPass1!"),
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()

    profile = UserProfile(
        user_id=user_id,
        cv_text=cv_text,
        title=title,
        skills=skills or [],
    )
    db.add(profile)
    await db.commit()
    return str(user_id)


MOCK_EMBEDDING = np.zeros(384, dtype=np.float32)


def _mock_session_factory(db_session):
    """Create a mock async_session that yields the test db_session."""

    @asynccontextmanager
    async def mock_session():
        yield db_session

    return mock_session


class TestGenerateProfileEmbedding:
    @patch("services.job_matcher.JobMatcher")
    async def test_success_generates_embedding(self, MockMatcher, db_session):
        mock_instance = MagicMock()
        mock_instance.encode.return_value = MOCK_EMBEDDING
        MockMatcher.return_value = mock_instance

        user_id = await _create_user_with_profile(
            db_session, cv_text="Python developer with 5 years experience"
        )

        with patch("database.task_session", _mock_session_factory(db_session)):
            result = await _generate_profile_embedding_async(user_id)

        assert result["status"] == "success"
        mock_instance.encode.assert_called_once()

    @patch("services.job_matcher.JobMatcher")
    async def test_profile_not_found(self, MockMatcher, db_session):
        fake_id = str(uuid.uuid4())

        with patch("database.task_session", _mock_session_factory(db_session)):
            result = await _generate_profile_embedding_async(fake_id)

        assert result["status"] == "error"
        assert result["reason"] == "profile_not_found"

    @patch("services.job_matcher.JobMatcher")
    async def test_no_cv_text(self, MockMatcher, db_session):
        user_id = await _create_user_with_profile(db_session, cv_text=None)

        with patch("database.task_session", _mock_session_factory(db_session)):
            result = await _generate_profile_embedding_async(user_id)

        assert result["status"] == "error"
        assert result["reason"] == "no_cv_text"

    @patch("services.job_matcher.JobMatcher")
    async def test_combines_title_and_skills(self, MockMatcher, db_session):
        mock_instance = MagicMock()
        mock_instance.encode.return_value = MOCK_EMBEDDING
        MockMatcher.return_value = mock_instance

        user_id = await _create_user_with_profile(
            db_session,
            cv_text="Experienced developer",
            title="Senior Backend Engineer",
            skills=["Python", "FastAPI"],
        )

        with patch("database.task_session", _mock_session_factory(db_session)):
            await _generate_profile_embedding_async(user_id)

        # Verify the encoded text contains title + cv_text + skills
        call_args = mock_instance.encode.call_args[0][0]
        assert "Senior Backend Engineer" in call_args
        assert "Experienced developer" in call_args
        assert "Python" in call_args


class TestGenerateJobEmbeddings:
    @patch("services.job_matcher.JobMatcher")
    async def test_batch_processes_jobs(self, MockMatcher, db_session):
        mock_instance = MagicMock()
        mock_instance.encode_batch.return_value = np.zeros((2, 384), dtype=np.float32)
        MockMatcher.return_value = mock_instance
        MockMatcher.build_job_text = MagicMock(return_value="job text")

        # Create 2 jobs without embeddings (hash max 32 chars)
        for i in range(2):
            job = Job(
                hash=f"emb_test_{i:022d}",
                source="test",
                title=f"Job {i}",
                company="TestCo",
                url=f"https://example.com/job/{i}",
                is_active=True,
            )
            db_session.add(job)
        await db_session.commit()

        with patch("database.task_session", _mock_session_factory(db_session)):
            result = await _generate_job_embeddings_async(batch_size=100)

        assert result["status"] == "success"
        assert result["processed"] == 2

    @patch("services.job_matcher.JobMatcher")
    async def test_empty_returns_zero(self, MockMatcher, db_session):
        with patch("database.task_session", _mock_session_factory(db_session)):
            result = await _generate_job_embeddings_async(batch_size=100)

        assert result["status"] == "success"
        assert result["processed"] == 0
