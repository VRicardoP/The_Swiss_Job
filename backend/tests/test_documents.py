"""Tests for AI document generation endpoints (CV and cover letter)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from models.user import User
from tests.conftest import random_email


_TEST_PASSWORD = "TestPass123!"
_MOCK_CV_CONTENT = "# Tailored CV\n\n## Professional Summary\nExperienced developer..."
_MOCK_CL_CONTENT = "# Cover Letter\n\nDear Hiring Manager,\n\nI am writing..."
_MOCK_GEMINI_CONTENT = "# CV (Gemini)\n\n## Profil\nErfahrener Entwickler..."


def _gemini_off() -> MagicMock:
    """GeminiService no configurado — fuerza el path de Groq."""
    m = MagicMock()
    m.is_available = False
    return m


def _gemini_on(content: str = _MOCK_GEMINI_CONTENT, error: Exception | None = None):
    """GeminiService disponible; devuelve `content` o lanza `error` (para fallback)."""
    m = MagicMock()
    m.is_available = True
    m.get_chat_response = AsyncMock(return_value=content, side_effect=error)
    return m


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register_and_get_token(client: AsyncClient) -> tuple[str, str]:
    email = random_email()
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": _TEST_PASSWORD, "gdpr_consent": True},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"], email


async def _insert_job(db: AsyncSession, idx: int = 0) -> str:
    valid_columns = {c.key for c in Job.__table__.columns}
    data = {
        "hash": f"doc{idx:029d}"[:32],
        "source": "test_source",
        "title": f"Senior Python Developer {idx}",
        "company": f"SwissTech Corp {idx}",
        "url": f"https://example.com/job/doc/{idx}",
        "description": (
            "We are looking for a Senior Python Developer with experience "
            "in FastAPI, PostgreSQL, and Docker."
        ),
        "location": "Zurich, ZH",
        "canton": "ZH",
        "remote": False,
        "tags": ["python", "fastapi", "postgresql", "docker"],
        "is_active": True,
    }
    job = Job(**{k: v for k, v in data.items() if k in valid_columns})
    db.add(job)
    await db.commit()
    return data["hash"]


async def _set_cv_text(db: AsyncSession, user_email: str) -> None:
    """Set CV text on user's profile for testing."""
    user = (await db.execute(select(User).where(User.email == user_email))).scalar_one()
    await db.refresh(user, ["profile"])
    user.profile.cv_text = (
        "Experienced Python developer with 5 years in backend development. "
        "Skills: Python, FastAPI, Django, PostgreSQL, Docker, Kubernetes."
    )
    user.profile.skills = ["Python", "FastAPI", "Django", "PostgreSQL", "Docker"]
    await db.commit()


# ---------------------------------------------------------------------------
# Auth & Validation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestDocumentValidation:
    async def test_generate_requires_auth(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/documents/generate",
            json={"job_hash": "x" * 32, "doc_type": "cv"},
        )
        assert resp.status_code == 401

    async def test_generate_requires_cv(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, _ = await _register_and_get_token(client)
        job_hash = await _insert_job(db_session)
        resp = await client.post(
            "/api/v1/documents/generate",
            headers=_auth(token),
            json={"job_hash": job_hash, "doc_type": "cv"},
        )
        assert resp.status_code in (400, 503)
        # 503 if no GROQ_API_KEY, 400 if no CV — both are valid guards

    async def test_generate_job_not_found(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, email = await _register_and_get_token(client)
        await _set_cv_text(db_session, email)
        resp = await client.post(
            "/api/v1/documents/generate",
            headers=_auth(token),
            json={
                "job_hash": "nonexistent000000000000000000000",
                "doc_type": "cv",
            },
        )
        # May be 503 (no GROQ key) or 404 (job not found)
        assert resp.status_code in (404, 503)

    async def test_invalid_doc_type(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, email = await _register_and_get_token(client)
        await _set_cv_text(db_session, email)
        job_hash = await _insert_job(db_session, idx=99)
        resp = await client.post(
            "/api/v1/documents/generate",
            headers=_auth(token),
            json={"job_hash": job_hash, "doc_type": "invalid"},
        )
        assert resp.status_code == 422

    async def test_invalid_language(
        self, client: AsyncClient, db_session: AsyncSession
    ):
        token, email = await _register_and_get_token(client)
        await _set_cv_text(db_session, email)
        job_hash = await _insert_job(db_session, idx=98)
        resp = await client.post(
            "/api/v1/documents/generate",
            headers=_auth(token),
            json={"job_hash": job_hash, "doc_type": "cv", "language": "xx"},
        )
        assert resp.status_code == 422
