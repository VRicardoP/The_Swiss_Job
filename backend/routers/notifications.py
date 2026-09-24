"""Notification endpoints — SSE stream + history + mark read."""

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import get_current_user
from database import get_db
from models.notification import Notification
from models.user import User
from schemas.notifications import (
    NotificationListResponse,
    NotificationResponse,
)
from services.sse_manager import SSEManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

# H13/T10 — el token de acceso viajaba en la query string del SSE, así que
# acababa en los logs de acceso de nginx, en el historial del navegador y en el
# Referer. Y duraba 30 minutos. Se sustituye por un TICKET: se pide con el
# Bearer por la vía normal, vale UNA sola vez y caduca en segundos, así que lo
# que quede escrito en un log ya no sirve para nada.
_PREFIJO_TICKET = "sse:ticket:"
TICKET_TTL_SEGUNDOS = 30


def _clave_ticket(ticket: str) -> str:
    return f"{_PREFIJO_TICKET}{ticket}"


@router.post("/stream-ticket", status_code=status.HTTP_201_CREATED)
async def crear_ticket_de_stream(
    request: Request, current_user: User = Depends(get_current_user)
):
    """Vale de un solo uso para abrir el stream sin poner el JWT en la URL."""
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Notification stream unavailable",
        )
    ticket = uuid.uuid4().hex
    await redis_client.set(
        _clave_ticket(ticket), str(current_user.id), ex=TICKET_TTL_SEGUNDOS
    )
    return {"ticket": ticket, "expires_in": TICKET_TTL_SEGUNDOS}


async def _usuario_del_ticket(request: Request, ticket: str) -> uuid.UUID:
    """Canjea el ticket. `GETDEL` es atómico: dos canjes no pueden ganar los dos."""
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Notification stream unavailable",
        )
    crudo = await redis_client.getdel(_clave_ticket(ticket))
    if crudo is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired stream ticket",
        )
    try:
        return uuid.UUID(crudo.decode() if isinstance(crudo, bytes) else str(crudo))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired stream ticket",
        ) from None


@router.get("/stream")
async def notification_stream(
    request: Request,
    ticket: str = Query(
        ..., description="Vale de un solo uso de POST /notifications/stream-ticket"
    ),
    db: AsyncSession = Depends(get_db),
):
    """SSE stream for real-time notifications.

    H13/T10: `EventSource` no sabe mandar cabeceras, así que aquí viajaba el
    JWT en la query string — y de ahí a los logs de nginx, al historial y al
    Referer, válido 30 minutos. Ahora viaja un ticket que se canjea UNA vez y
    caduca en 30 segundos; lo que quede escrito en un log ya no abre nada.
    """
    user_id = await _usuario_del_ticket(request, ticket)

    # G1/P3-24: el resto de endpoints pasa por get_current_user (existencia +
    # is_active); aquí solo se decodificaba el token — un usuario desactivado
    # mantenía el stream abierto hasta caducar el JWT.
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive user",
        )

    # G2/P2-4: FastAPI no cierra las dependencias con `yield` hasta que la
    # RESPUESTA termina — y este stream es infinito. Tras el SELECT, la sesión
    # quedaba con su transacción abierta («idle in transaction») anclando una
    # conexión del pool POR PESTAÑA durante toda la vida del stream (30
    # streams agotan pool+overflow y cuelgan el API entero). Se libera AQUÍ,
    # antes de devolver el StreamingResponse; el cierre posterior de get_db
    # es idempotente.
    await db.rollback()
    await db.close()

    sse: SSEManager = request.app.state.sse_manager

    queue = await sse.subscribe(user_id)

    async def event_generator():
        try:
            # Send initial connected event
            yield SSEManager.format_sse("connected", {"user_id": str(user_id)})

            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield SSEManager.format_sse(
                        message.get("event", "message"),
                        message.get("data", {}),
                    )
                except asyncio.TimeoutError:
                    # Send keepalive ping
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse.unsubscribe(user_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Get paginated notification history."""
    conditions = [Notification.user_id == current_user.id]

    total = (
        await db.execute(
            select(func.count()).select_from(Notification).where(*conditions)
        )
    ).scalar_one()

    unread_count = (
        await db.execute(
            select(func.count())
            .select_from(Notification)
            .where(*conditions, Notification.is_read.is_(False))
        )
    ).scalar_one()

    stmt = (
        select(Notification)
        .where(*conditions)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()

    return NotificationListResponse(
        data=[NotificationResponse.model_validate(n) for n in rows],
        total=total,
        unread_count=unread_count,
    )


@router.put("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a notification as read."""
    notification = (
        await db.execute(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    notification.is_read = True
    await db.commit()
    await db.refresh(notification)

    return NotificationResponse.model_validate(notification)
