"""Tests for Profile CRUD endpoints."""

import io
from unittest.mock import MagicMock, patch

from httpx import AsyncClient

from tests.conftest import random_email


async def _register_and_get_token(client: AsyncClient) -> tuple[str, str]:
    """Register a user and return (access_token, email)."""
    email = random_email()
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "SecureP@ss1", "gdpr_consent": True},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"], email


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_pdf(text: str) -> bytes:
    """Create a minimal PDF with the given text."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _make_docx(text: str) -> bytes:
    """Create a minimal DOCX with the given text."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


class TestGetProfile:
    async def test_get_profile_authenticated(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.get("/api/v1/profile", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert "user_id" in data
        assert data["skills"] == []
        assert data["title"] is None

    async def test_get_profile_unauthenticated(self, client):
        resp = await client.get("/api/v1/profile")
        assert resp.status_code == 401

    async def test_has_cv_embedding_false_initially(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.get("/api/v1/profile", headers=_auth(token))
        assert resp.json()["has_cv_embedding"] is False


class TestUpdateProfile:
    async def test_update_title(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"title": "Backend Developer"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Backend Developer"

    async def test_update_skills(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"skills": ["Python", "FastAPI", "PostgreSQL"]},
        )
        assert resp.status_code == 200
        assert set(resp.json()["skills"]) == {"Python", "FastAPI", "PostgreSQL"}

    async def test_update_salary_range(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"salary_min": 80000, "salary_max": 120000},
        )
        assert resp.status_code == 200
        assert resp.json()["salary_min"] == 80000
        assert resp.json()["salary_max"] == 120000

    async def test_update_salary_invalid_range(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"salary_min": 120000, "salary_max": 80000},
        )
        assert resp.status_code == 422

    async def test_update_remote_pref(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"remote_pref": "hybrid"},
        )
        assert resp.status_code == 200
        assert resp.json()["remote_pref"] == "hybrid"

    async def test_update_remote_pref_invalid(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"remote_pref": "invalid_value"},
        )
        assert resp.status_code == 422

    async def test_update_score_weights(self, client):
        token, _ = await _register_and_get_token(client)
        weights = {
            "embedding": 0.25,
            "llm": 0.35,
            "salary": 0.15,
            "location": 0.15,
            "recency": 0.10,
        }
        resp = await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"score_weights": weights},
        )
        assert resp.status_code == 200
        assert resp.json()["score_weights"] == weights

    async def test_update_score_weights_bad_sum(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"score_weights": {"embedding": 0.5, "salary": 0.1}},
        )
        assert resp.status_code == 422

    async def test_update_score_weights_bad_keys(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"score_weights": {"unknown_key": 1.0}},
        )
        assert resp.status_code == 422

    async def test_partial_update_preserves_fields(self, client):
        token, _ = await _register_and_get_token(client)
        # First set title and skills
        await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"title": "Dev", "skills": ["Python"]},
        )
        # Then update only locations
        resp = await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"locations": ["Zurich"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Dev"
        assert data["skills"] == ["Python"]
        assert data["locations"] == ["Zurich"]

    async def test_update_locations(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"locations": ["Zurich", "Bern", "Basel"]},
        )
        assert resp.status_code == 200
        assert set(resp.json()["locations"]) == {"Zurich", "Bern", "Basel"}


class TestUploadCV:
    @patch("tasks.embedding_tasks.generate_profile_embedding")
    async def test_upload_pdf_success(self, mock_task, client):
        mock_task.delay.return_value = MagicMock(id="task-123")
        token, _ = await _register_and_get_token(client)

        cv_text = (
            "Senior Python Developer with 5 years of experience in FastAPI "
            "and PostgreSQL. Expert in Docker and Kubernetes deployments."
        )
        pdf_bytes = _make_pdf(cv_text)

        resp = await client.post(
            "/api/v1/profile/cv",
            headers=_auth(token),
            files={"file": ("cv.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "CV uploaded and parsed successfully"
        assert data["cv_text_length"] > 0
        assert "Python" in data["skills_extracted"]

    @patch("tasks.embedding_tasks.generate_profile_embedding")
    async def test_upload_docx_success(self, mock_task, client):
        mock_task.delay.return_value = MagicMock(id="task-456")
        token, _ = await _register_and_get_token(client)

        cv_text = (
            "Full Stack Developer experienced with React, Node.js and "
            "TypeScript. Fluent in Deutsch and English."
        )
        docx_bytes = _make_docx(cv_text)

        resp = await client.post(
            "/api/v1/profile/cv",
            headers=_auth(token),
            files={
                "file": (
                    "cv.docx",
                    docx_bytes,
                    "application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document",
                )
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["cv_text_length"] > 0
        assert "React" in data["skills_extracted"]

    async def test_upload_unsupported_type(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.post(
            "/api/v1/profile/cv",
            headers=_auth(token),
            files={"file": ("cv.txt", b"some text", "text/plain")},
        )
        assert resp.status_code == 415

    async def test_upload_empty_file(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.post(
            "/api/v1/profile/cv",
            headers=_auth(token),
            files={"file": ("cv.pdf", b"", "application/pdf")},
        )
        assert resp.status_code == 400

    async def test_upload_unauthenticated(self, client):
        resp = await client.post(
            "/api/v1/profile/cv",
            files={"file": ("cv.pdf", b"data", "application/pdf")},
        )
        assert resp.status_code == 401

    @patch("tasks.embedding_tasks.generate_profile_embedding")
    async def test_upload_merges_skills(self, mock_task, client):
        mock_task.delay.return_value = MagicMock(id="task-789")
        token, _ = await _register_and_get_token(client)

        # Set existing skills
        await client.put(
            "/api/v1/profile",
            headers=_auth(token),
            json={"skills": ["Go", "Rust"]},
        )

        # Upload CV with different skills
        cv_text = (
            "Python developer with extensive experience in FastAPI and "
            "PostgreSQL. Expert in Docker containerization and cloud deployments."
        )
        pdf_bytes = _make_pdf(cv_text)
        resp = await client.post(
            "/api/v1/profile/cv",
            headers=_auth(token),
            files={"file": ("cv.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 200

        # Verify skills merged
        profile_resp = await client.get("/api/v1/profile", headers=_auth(token))
        all_skills = profile_resp.json()["skills"]
        assert "Go" in all_skills
        assert "Rust" in all_skills
        assert "Python" in all_skills


class TestDeleteCV:
    async def test_delete_cv_success(self, client):
        token, _ = await _register_and_get_token(client)
        resp = await client.delete("/api/v1/profile/cv", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["message"] == "CV data deleted successfully"

    async def test_delete_cv_idempotent(self, client):
        token, _ = await _register_and_get_token(client)
        # Delete twice — both should succeed
        resp1 = await client.delete("/api/v1/profile/cv", headers=_auth(token))
        resp2 = await client.delete("/api/v1/profile/cv", headers=_auth(token))
        assert resp1.status_code == 200
        assert resp2.status_code == 200
