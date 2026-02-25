from httpx import AsyncClient

from tests.conftest import random_email


class TestRegister:
    async def test_register_success(self, client: AsyncClient):
        email = random_email()
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "SecureP@ss1", "gdpr_consent": True},
        )
        assert response.status_code == 201
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == email
        assert data["user"]["is_active"] is True
        assert data["user"]["gdpr_consent"] is True
        assert data["user"]["gdpr_consent_at"] is not None

    async def test_register_duplicate_email(self, client: AsyncClient):
        email = random_email()
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "SecureP@ss1", "gdpr_consent": True},
        )
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "AnotherP@ss1", "gdpr_consent": True},
        )
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]

    async def test_register_without_gdpr_consent(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": random_email(),
                "password": "SecureP@ss1",
                "gdpr_consent": False,
            },
        )
        assert response.status_code == 422

    async def test_register_short_password(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": random_email(),
                "password": "short",
                "gdpr_consent": True,
            },
        )
        assert response.status_code == 422

    async def test_register_invalid_email(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "not-an-email",
                "password": "SecureP@ss1",
                "gdpr_consent": True,
            },
        )
        assert response.status_code == 422


class TestLogin:
    async def test_login_success(self, client: AsyncClient):
        email = random_email()
        password = "SecureP@ss1"
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "gdpr_consent": True},
        )
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["user"]["email"] == email

    async def test_login_wrong_password(self, client: AsyncClient):
        email = random_email()
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "SecureP@ss1", "gdpr_consent": True},
        )
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "WrongPassword1"},
        )
        assert response.status_code == 401
        assert "Invalid email or password" in response.json()["detail"]

    async def test_login_nonexistent_email(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": "SecureP@ss1"},
        )
        assert response.status_code == 401
        assert "Invalid email or password" in response.json()["detail"]

    async def test_login_inactive_user(self, client: AsyncClient, db_session):
        from sqlalchemy import update

        from models.user import User

        email = random_email()
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "SecureP@ss1", "gdpr_consent": True},
        )
        await db_session.execute(
            update(User).where(User.email == email).values(is_active=False)
        )
        await db_session.commit()

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "SecureP@ss1"},
        )
        assert response.status_code == 403
        assert "deactivated" in response.json()["detail"]


class TestRefresh:
    async def test_refresh_success(self, client: AsyncClient):
        email = random_email()
        reg = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "SecureP@ss1", "gdpr_consent": True},
        )
        refresh_token = reg.json()["refresh_token"]

        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_refresh_with_access_token_fails(self, client: AsyncClient):
        email = random_email()
        reg = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "SecureP@ss1", "gdpr_consent": True},
        )
        access_token = reg.json()["access_token"]

        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": access_token},
        )
        assert response.status_code == 401

    async def test_refresh_with_invalid_token(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid.token.here"},
        )
        assert response.status_code == 401


class TestMe:
    async def test_me_authenticated(self, client: AsyncClient):
        email = random_email()
        reg = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": "SecureP@ss1", "gdpr_consent": True},
        )
        access_token = reg.json()["access_token"]

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        assert response.json()["email"] == email

    async def test_me_no_token(self, client: AsyncClient):
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401

    async def test_me_invalid_token(self, client: AsyncClient):
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert response.status_code == 401

    async def test_me_expired_token(self, client: AsyncClient):
        import uuid
        from datetime import datetime, timedelta, timezone

        from jose import jwt

        from config import settings

        expired_payload = {
            "sub": str(uuid.uuid4()),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
            "type": "access",
        }
        expired_token = jwt.encode(
            expired_payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM
        )

        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert response.status_code == 401
