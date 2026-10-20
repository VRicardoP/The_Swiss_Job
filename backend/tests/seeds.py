"""Siembras compartidas: usuario, oferta legacy y match con estado.

Vivían en `test_applications_contract.py`; al retirarse el vertical local de
candidaturas (DT-148), las conservan aquí los tests de feedback del core, de la
watchlist escolar, de documentos y de colegios que las importaban.
"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.match_result import MatchResult
from models.user import User
from services.matching.identity import set_profile_link

JOB_HASH = "a" * 32
OTHER_HASH = "b" * 32


async def seed_user(db: AsyncSession) -> uuid.UUID:
    user = User(
        email=f"contract-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        gdpr_consent=True,
    )
    db.add(user)
    await db.flush()
    return user.id


async def seed_job(db: AsyncSession, job_hash: str = JOB_HASH) -> str:
    db.add(
        Job(
            hash=job_hash,
            source="test_source",
            title="Senior Python Developer",
            company="SwissTech Corp",
            url=f"https://example.com/job/{job_hash[:6]}",
            description="FastAPI, PostgreSQL, Docker.",
            is_active=True,
        )
    )
    return job_hash


async def seed_match(
    db: AsyncSession,
    user_id: uuid.UUID,
    job_hash: str = JOB_HASH,
    application_status: str = "none",
    draft_letter: str | None = None,
) -> MatchResult:
    row = MatchResult(
        user_id=user_id,
        job_hash=job_hash,
        score_embedding=0.8,
        score_salary=0.5,
        score_location=0.5,
        score_recency=0.5,
        score_llm=0.5,
        score_final=80,
        application_status=application_status,
        draft_letter=draft_letter,
        matching_skills=[],
        missing_skills=[],
    )
    db.add(row)
    await db.flush()
    return row


@pytest.fixture
async def seeded(db_session):
    """Linked user fixture shared by Core feedback and school-state tests."""
    user_id = await seed_user(db_session)
    await seed_job(db_session, JOB_HASH)
    await seed_job(db_session, OTHER_HASH)
    await db_session.commit()
    core_profile_id = uuid.uuid4()
    await set_profile_link(db_session, user_id, core_profile_id, updated_by="tests")
    return user_id, SimpleNamespace(profile_id=core_profile_id), None
