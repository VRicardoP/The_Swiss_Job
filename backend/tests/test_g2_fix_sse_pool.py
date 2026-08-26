"""Regresión de la auditoría G2 — P2-4: el stream SSE anclaba una conexión.

El fix G1/P3-24 añadió `Depends(get_db)` al endpoint SSE para comprobar que el
usuario sigue activo. FastAPI no cierra las dependencias con `yield` hasta que
la RESPUESTA termina, y esta es un `StreamingResponse` infinito: tras el SELECT
la sesión quedaba con su transacción abierta (autobegin, sin commit/rollback),
así que cada pestaña conectada retenía una conexión del pool en «idle in
transaction» durante horas. Con DB_POOL_SIZE=10 + DB_MAX_OVERFLOW=20, treinta
streams dejaban el API entero sin conexiones; mucho antes, las transacciones
idle indefinidas bloquean el trabajo de autovacuum.
"""

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from core.security import create_access_token, hash_password
from models.user import User
from routers.notifications import notification_stream
from services.sse_manager import SSEManager


class _FakeRedis:
    """SSEManager solo necesita el cliente para pub/sub; aquí no se arranca."""


def _fake_request(sse: SSEManager) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(sse_manager=sse)))


async def _make_user(db, *, is_active: bool = True) -> uuid.UUID:
    uid = uuid.uuid4()
    db.add(
        User(
            id=uid,
            email=f"sse-{uid.hex[:8]}@example.com",
            hashed_password=hash_password("TestPass1!"),
            gdpr_consent=True,
            is_active=is_active,
        )
    )
    await db.commit()
    return uid


@pytest.mark.asyncio
class TestP24StreamNoRetieneConexion:
    async def test_la_sesion_queda_liberada_antes_de_devolver_el_stream(
        self, db_session
    ):
        """Al retornar el StreamingResponse no puede quedar transacción viva."""
        uid = await _make_user(db_session)
        sse = SSEManager(_FakeRedis())

        response = await notification_stream(
            request=_fake_request(sse),
            token=create_access_token(uid),
            db=db_session,
        )

        assert response.media_type == "text/event-stream"
        assert not db_session.in_transaction(), (
            "la sesión sigue en transacción: cada stream ancla una conexión "
            "del pool en «idle in transaction» mientras viva"
        )

    async def test_el_stream_sigue_emitiendo_su_primer_evento(self, db_session):
        """No-regresión: liberar la sesión no rompe el generador."""
        uid = await _make_user(db_session)
        sse = SSEManager(_FakeRedis())

        response = await notification_stream(
            request=_fake_request(sse),
            token=create_access_token(uid),
            db=db_session,
        )

        agen = response.body_iterator
        first = await asyncio.wait_for(agen.__anext__(), timeout=5)
        await agen.aclose()

        assert "event: connected" in first
        assert str(uid) in first

    async def test_usuario_desactivado_sigue_rechazado(self, db_session):
        """No-regresión de G1/P3-24: la comprobación de is_active se mantiene."""
        from fastapi import HTTPException

        uid = await _make_user(db_session, is_active=False)
        sse = SSEManager(_FakeRedis())

        with pytest.raises(HTTPException) as exc:
            await notification_stream(
                request=_fake_request(sse),
                token=create_access_token(uid),
                db=db_session,
            )
        assert exc.value.status_code == 401

        # La sesión se reutiliza tras el 401 (la usa el propio fixture).
        assert (
            await db_session.execute(select(User).where(User.id == uid))
        ).scalar_one_or_none() is not None
