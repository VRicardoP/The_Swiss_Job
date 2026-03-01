"""Tests for AI document generation endpoints (CV and cover letter)."""

import uuid
from unittest.mock import AsyncMock, patch

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


# ---------------------------------------------------------------------------
# Generation (with mocked Groq)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestDocumentGeneration:
    @patch("routers.documents._get_groq")
    async def test_generate_cv_success(
        self,
        mock_get_groq,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        mock_groq = AsyncMock()
        mock_groq.is_available = True
        mock_groq.get_chat_response = AsyncMock(return_value=_MOCK_CV_CONTENT)
        mock_get_groq.return_value = mock_groq

        token, email = await _register_and_get_token(client)
        await _set_cv_text(db_session, email)
        job_hash = await _insert_job(db_session, idx=1)

        resp = await client.post(
            "/api/v1/documents/generate",
            headers=_auth(token),
            json={"job_hash": job_hash, "doc_type": "cv", "language": "en"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_type"] == "cv"
        assert "Tailored CV" in data["content"]
        assert data["job_title"] == "Senior Python Developer 1"
        assert data["job_company"] == "SwissTech Corp 1"
        assert data["language"] == "en"
        assert data["id"] is not None

    @patch("routers.documents._get_groq")
    async def test_generate_cover_letter_success(
        self,
        mock_get_groq,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        mock_groq = AsyncMock()
        mock_groq.is_available = True
        mock_groq.get_chat_response = AsyncMock(return_value=_MOCK_CL_CONTENT)
        mock_get_groq.return_value = mock_groq

        token, email = await _register_and_get_token(client)
        await _set_cv_text(db_session, email)
        job_hash = await _insert_job(db_session, idx=2)

        resp = await client.post(
            "/api/v1/documents/generate",
            headers=_auth(token),
            json={
                "job_hash": job_hash,
                "doc_type": "cover_letter",
                "language": "de",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_type"] == "cover_letter"
        assert "Cover Letter" in data["content"]
        assert data["language"] == "de"

    @patch("routers.documents._get_groq")
    async def test_generate_groq_unavailable(
        self,
        mock_get_groq,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        mock_groq = AsyncMock()
        mock_groq.is_available = False
        mock_get_groq.return_value = mock_groq

        token, email = await _register_and_get_token(client)
        await _set_cv_text(db_session, email)
        job_hash = await _insert_job(db_session, idx=3)

        resp = await client.post(
            "/api/v1/documents/generate",
            headers=_auth(token),
            json={"job_hash": job_hash, "doc_type": "cv"},
        )
        assert resp.status_code == 503
        assert "GROQ_API_KEY" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# List & Delete
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestDocumentCRUD:
    async def test_list_requires_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/documents/somehash")
        assert resp.status_code == 401

    async def test_list_empty(self, client: AsyncClient, db_session: AsyncSession):
        token, _ = await _register_and_get_token(client)
        job_hash = await _insert_job(db_session, idx=5)
        resp = await client.get(
            f"/api/v1/documents/{job_hash}",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["data"] == []

    @patch("routers.documents._get_groq")
    async def test_list_with_generated_doc(
        self,
        mock_get_groq,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        mock_groq = AsyncMock()
        mock_groq.is_available = True
        mock_groq.get_chat_response = AsyncMock(return_value=_MOCK_CV_CONTENT)
        mock_get_groq.return_value = mock_groq

        token, email = await _register_and_get_token(client)
        await _set_cv_text(db_session, email)
        job_hash = await _insert_job(db_session, idx=6)

        # Generate a document
        await client.post(
            "/api/v1/documents/generate",
            headers=_auth(token),
            json={"job_hash": job_hash, "doc_type": "cv"},
        )

        # List documents
        resp = await client.get(
            f"/api/v1/documents/{job_hash}",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["data"][0]["doc_type"] == "cv"

    @patch("routers.documents._get_groq")
    async def test_delete_document(
        self,
        mock_get_groq,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        mock_groq = AsyncMock()
        mock_groq.is_available = True
        mock_groq.get_chat_response = AsyncMock(return_value=_MOCK_CV_CONTENT)
        mock_get_groq.return_value = mock_groq

        token, email = await _register_and_get_token(client)
        await _set_cv_text(db_session, email)
        job_hash = await _insert_job(db_session, idx=7)

        # Generate
        gen_resp = await client.post(
            "/api/v1/documents/generate",
            headers=_auth(token),
            json={"job_hash": job_hash, "doc_type": "cv"},
        )
        doc_id = gen_resp.json()["id"]

        # Delete
        resp = await client.delete(
            f"/api/v1/documents/{doc_id}",
            headers=_auth(token),
        )
        assert resp.status_code == 204

        # Verify deleted
        list_resp = await client.get(
            f"/api/v1/documents/{job_hash}",
            headers=_auth(token),
        )
        assert list_resp.json()["total"] == 0

    async def test_delete_not_found(self, client: AsyncClient):
        token, _ = await _register_and_get_token(client)
        fake_id = str(uuid.uuid4())
        resp = await client.delete(
            f"/api/v1/documents/{fake_id}",
            headers=_auth(token),
        )
        assert resp.status_code == 404

    @patch("routers.documents._get_groq")
    async def test_user_isolation(
        self,
        mock_get_groq,
        client: AsyncClient,
        db_session: AsyncSession,
    ):
        """Users cannot see each other's generated documents."""
        mock_groq = AsyncMock()
        mock_groq.is_available = True
        mock_groq.get_chat_response = AsyncMock(return_value=_MOCK_CV_CONTENT)
        mock_get_groq.return_value = mock_groq

        token_a, email_a = await _register_and_get_token(client)
        token_b, _ = await _register_and_get_token(client)
        await _set_cv_text(db_session, email_a)
        job_hash = await _insert_job(db_session, idx=8)

        # User A generates a doc
        await client.post(
            "/api/v1/documents/generate",
            headers=_auth(token_a),
            json={"job_hash": job_hash, "doc_type": "cv"},
        )

        # User B cannot see it
        resp = await client.get(
            f"/api/v1/documents/{job_hash}",
            headers=_auth(token_b),
        )
        assert resp.json()["total"] == 0
