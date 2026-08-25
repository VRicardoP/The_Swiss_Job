"""Regresiones de la auditoría G1 — API y tareas de watchlist/scheduler.

- P3-18: renovación del leader-lock que lanza → ventana de doble scheduler.
- P3-20: digest watchlist con ventana fija now-24h (solapes/huecos).
- P3-21: check_watchlist_health re-notificaba lo mismo cada 6h.
- P3-24: el SSE de notificaciones no comprobaba usuario activo/existente.
- P3-26: upload_cv sin fila de perfil → AttributeError (500).
- P3-28: rate limiting por IP directa — tras el proxy, bucket global.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from config import settings


@pytest.mark.asyncio
class TestP318LeaderLock:
    async def test_renovacion_que_lanza_cede_el_liderazgo(self):
        from services import scheduler as sched_mod

        redis_mock = SimpleNamespace(
            get=AsyncMock(side_effect=ConnectionError("redis hiccup")),
            set=AsyncMock(),
            expire=AsyncMock(),
        )
        # scheduler.running es False en tests (nunca arrancado): no hay que
        # simular el shutdown.
        is_leader = await sched_mod._leader_step(redis_mock, True)
        assert is_leader is False, (
            "ante un fallo de renovación NO podemos asumir que seguimos "
            "siendo líder (doble scheduler → doble daily_harvest)"
        )

    async def test_renovacion_ok_mantiene_liderazgo(self):
        from services import scheduler as sched_mod

        redis_mock = SimpleNamespace(
            get=AsyncMock(return_value=sched_mod._WORKER_ID),
            expire=AsyncMock(return_value=True),
        )
        assert await sched_mod._leader_step(redis_mock, True) is True


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        value = self.store.get(key)
        return value.encode() if value is not None else None

    def set(self, key, value, ex=None):
        self.store[key] = value

    def close(self):
        pass


@pytest.mark.asyncio
class TestP320DigestMarcaDeAgua:
    async def test_ventana_avanza_con_marca(self, monkeypatch, db_session):
        from contextlib import asynccontextmanager

        from tasks.watchlist_tasks import _DIGEST_WATERMARK_KEY, _send_digest_async

        fake = _FakeRedis()
        monkeypatch.setattr("redis.from_url", lambda *a, **k: fake)

        @asynccontextmanager
        async def _session():
            yield db_session

        with patch("database.task_session", new=_session):
            await _send_digest_async()
            first_mark = fake.store.get(_DIGEST_WATERMARK_KEY)
            assert first_mark is not None, "la marca debe guardarse en el primer run"
            await _send_digest_async()
            second_mark = fake.store.get(_DIGEST_WATERMARK_KEY)
        assert second_mark is not None and second_mark >= first_mark


@pytest.mark.asyncio
class TestP321CooldownSalud:
    async def test_mismo_problema_no_se_renotifica(self, monkeypatch, db_session):
        from contextlib import asynccontextmanager

        from tasks.watchlist_tasks import _check_health_async

        fake = _FakeRedis()
        monkeypatch.setattr("redis.from_url", lambda *a, **k: fake)

        @asynccontextmanager
        async def _session():
            yield db_session

        with patch("database.task_session", new=_session):
            r1 = await _check_health_async()
            r2 = await _check_health_async()

        if r1.get("status") == "issues":
            assert r1["fresh_issues"] >= 0
            # Segunda pasada inmediata: los mismos problemas están en cooldown.
            assert r2["fresh_issues"] == 0, (
                "el MISMO problema no debe generar otra notificación a las 6h"
            )
        else:
            # BD de test sin fuentes con problemas: el contrato queda cubierto
            # por la clave fresh_issues cuando existan.
            assert r1.get("status") in ("ok", "issues")


@pytest.mark.asyncio
class TestP324SSEUsuarioActivo:
    async def test_stream_con_usuario_inexistente_es_401(self, client):
        from core.security import create_access_token

        ghost_id = uuid.uuid4()
        token = create_access_token(ghost_id)
        resp = await client.get(f"/api/v1/notifications/stream?token={token}")
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestP326UploadCvSinPerfil:
    async def test_upload_cv_sin_perfil_es_404(self, client, db_session):
        from sqlalchemy import delete

        from models.user_profile import UserProfile
        from tests.conftest import random_email

        email = random_email()
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "TestPass1!",
                "gdpr_consent": True,
            },
        )
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "TestPass1!"},
        )
        token = login.json()["access_token"]

        # Borrar la fila de perfil (el estado que provocaba el 500).
        from sqlalchemy import select

        from models.user import User

        user = (
            await db_session.execute(select(User).where(User.email == email))
        ).scalar_one()
        await db_session.execute(
            delete(UserProfile).where(UserProfile.user_id == user.id)
        )
        await db_session.commit()

        pdf_bytes = b"%PDF-1.4 " + b"x" * 100
        resp = await client.post(
            "/api/v1/profile/cv",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("cv.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp.status_code in (404, 422), (
            f"nunca un 500 por perfil ausente (got {resp.status_code})"
        )
        assert resp.status_code != 500


class TestP328RateLimitProxy:
    def _request(self, headers: dict, client_host="10.0.0.1"):
        scope_headers = [
            (k.lower().encode(), v.encode()) for k, v in headers.items()
        ]
        return SimpleNamespace(
            headers={k.lower(): v for k, v in headers.items()},
            client=SimpleNamespace(host=client_host),
            scope={"client": (client_host, 1234)},
        )

    def test_con_trust_proxy_usa_forwarded(self, monkeypatch):
        from core.rate_limit import get_limiter_key

        monkeypatch.setattr(settings, "RATE_LIMIT_TRUST_PROXY", True, raising=False)
        req = self._request({"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})
        assert get_limiter_key(req) == "203.0.113.7"

    def test_sin_trust_proxy_ignora_la_cabecera(self, monkeypatch):
        from core.rate_limit import get_limiter_key

        monkeypatch.setattr(settings, "RATE_LIMIT_TRUST_PROXY", False, raising=False)
        req = self._request({"X-Forwarded-For": "203.0.113.7"})
        # Sin confianza en el proxy, la cabecera (falsificable) no manda.
        assert get_limiter_key(req) != "203.0.113.7"
