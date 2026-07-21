"""Tests for maintenance tasks — cleanup archives attached jobs (PF.3).

El cleanup NO debe destruir datos del usuario: una oferta caducada CON adjuntos
(candidatura, documento generado, o match con feedback/borrador) se archiva
(is_active=False) en vez de borrarse; sin adjuntos se borra.
"""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from sqlalchemy import select, text, update

from core.security import hash_password
from models.generated_document import GeneratedDocument
from models.job import Job
from models.job_application import JobApplication
from models.match_result import MatchResult
from models.user import User
from tasks.maintenance_tasks import _cleanup_stale_jobs_async


def _mock_session_factory(db_session):
    @asynccontextmanager
    async def mock_session():
        yield db_session

    return mock_session


async def _make_stale_job(db, hash_, days_old=200):
    """Crea una oferta activa y la envejece (last_seen_at) para que sea caducada."""
    db.add(
        Job(
            hash=hash_,
            source="test",
            title="Old Job",
            company="Acme",
            url=f"https://example.com/{hash_}",
            is_active=True,
        )
    )
    await db.commit()
    await db.execute(
        update(Job)
        .where(Job.hash == hash_)
        .values(last_seen_at=text(f"NOW() - INTERVAL '{days_old} days'"))
    )
    await db.commit()


async def _make_user(db):
    uid = uuid.uuid4()
    db.add(
        User(
            id=uid,
            email=f"u-{uid.hex[:8]}@example.com",
            hashed_password=hash_password("TestPass1!"),
            gdpr_consent=True,
        )
    )
    await db.commit()
    return uid


async def _make_match(db, uid, hash_, **over):
    mr = MatchResult(
        user_id=uid,
        job_hash=hash_,
        score_embedding=0.1,
        score_salary=0.1,
        score_location=0.1,
        score_recency=0.1,
        score_final=10.0,
        matching_skills=[],
        missing_skills=[],
    )
    for k, v in over.items():
        setattr(mr, k, v)
    db.add(mr)
    await db.commit()


@pytest.mark.anyio
class TestCleanupArchivesAttachedJobs:
    async def test_stale_job_with_document_is_archived_not_deleted(self, db_session):
        h = "docjob" + "0" * 26  # 32 chars
        await _make_stale_job(db_session, h)
        uid = await _make_user(db_session)
        db_session.add(
            GeneratedDocument(
                user_id=uid, job_hash=h, doc_type="cv", content="mi CV generado"
            )
        )
        await db_session.commit()

        with patch("database.task_session", _mock_session_factory(db_session)):
            await _cleanup_stale_jobs_async(max_age_days=60)

        db_session.expire_all()
        job = (
            await db_session.execute(select(Job).where(Job.hash == h))
        ).scalar_one_or_none()
        assert job is not None  # NO borrada
        assert job.is_active is False  # archivada
        doc = (
            await db_session.execute(
                select(GeneratedDocument).where(GeneratedDocument.job_hash == h)
            )
        ).scalar_one_or_none()
        assert doc is not None  # documento del usuario sobrevive
        assert doc.content == "mi CV generado"

    async def test_stale_job_with_application_is_archived_not_deleted(self, db_session):
        h = "appjob" + "0" * 26
        await _make_stale_job(db_session, h)
        uid = await _make_user(db_session)
        db_session.add(JobApplication(user_id=uid, job_hash=h))
        await db_session.commit()

        with patch("database.task_session", _mock_session_factory(db_session)):
            await _cleanup_stale_jobs_async(max_age_days=60)

        db_session.expire_all()
        job = (
            await db_session.execute(select(Job).where(Job.hash == h))
        ).scalar_one_or_none()
        assert job is not None
        assert job.is_active is False
        app = (
            await db_session.execute(
                select(JobApplication).where(JobApplication.job_hash == h)
            )
        ).scalar_one_or_none()
        assert app is not None  # candidatura del usuario sobrevive

    async def test_stale_job_without_attachments_is_deleted(self, db_session):
        h = "freejob" + "0" * 25
        await _make_stale_job(db_session, h)

        with patch("database.task_session", _mock_session_factory(db_session)):
            await _cleanup_stale_jobs_async(max_age_days=60)

        db_session.expire_all()
        job = (
            await db_session.execute(select(Job).where(Job.hash == h))
        ).scalar_one_or_none()
        assert job is None  # sin adjuntos → borrada

    async def test_stale_job_with_match_feedback_is_archived(self, db_session):
        h = "mfeed" + "0" * 27
        await _make_stale_job(db_session, h)
        uid = await _make_user(db_session)
        await _make_match(db_session, uid, h, feedback="thumbs_down")

        with patch("database.task_session", _mock_session_factory(db_session)):
            await _cleanup_stale_jobs_async(max_age_days=60)

        db_session.expire_all()
        job = (
            await db_session.execute(select(Job).where(Job.hash == h))
        ).scalar_one_or_none()
        assert job is not None and job.is_active is False

    async def test_stale_job_with_implicit_feedback_is_archived(self, db_session):
        h = "mimpl" + "0" * 27
        await _make_stale_job(db_session, h)
        uid = await _make_user(db_session)
        await _make_match(db_session, uid, h, feedback_implicit=[{"action": "opened"}])

        with patch("database.task_session", _mock_session_factory(db_session)):
            await _cleanup_stale_jobs_async(max_age_days=60)

        db_session.expire_all()
        job = (
            await db_session.execute(select(Job).where(Job.hash == h))
        ).scalar_one_or_none()
        assert job is not None and job.is_active is False

    async def test_stale_job_with_bare_detected_match_is_deleted(self, db_session):
        # Un match en estado por defecto (detected, sin feedback/draft) NO es
        # adjunto: el usuario no interactuó -> la oferta caducada se borra.
        h = "mbare" + "0" * 27
        await _make_stale_job(db_session, h)
        uid = await _make_user(db_session)
        await _make_match(db_session, uid, h)  # feedback=None, status='detected'

        with patch("database.task_session", _mock_session_factory(db_session)):
            await _cleanup_stale_jobs_async(max_age_days=60)

        db_session.expire_all()
        job = (
            await db_session.execute(select(Job).where(Job.hash == h))
        ).scalar_one_or_none()
        assert job is None  # borrada (cascade elimina el match derivado)

    async def test_non_stale_attached_job_is_untouched(self, db_session):
        # Con adjunto pero NO caducada: ni se archiva ni se borra.
        h = "fresh" + "0" * 27
        db_session.add(
            Job(
                hash=h,
                source="test",
                title="T",
                company="C",
                url=f"https://example.com/{h}",
                is_active=True,
            )
        )
        await db_session.commit()  # last_seen_at = now (no caducada)
        uid = await _make_user(db_session)
        db_session.add(JobApplication(user_id=uid, job_hash=h))
        await db_session.commit()

        with patch("database.task_session", _mock_session_factory(db_session)):
            await _cleanup_stale_jobs_async(max_age_days=60)

        db_session.expire_all()
        job = (
            await db_session.execute(select(Job).where(Job.hash == h))
        ).scalar_one_or_none()
        assert job is not None and job.is_active is True
