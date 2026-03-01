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


@router.get("/stream")
async def notification_stream(
    request: Request,
    token: str = Query(
        ..., description="JWT access token (EventSource can't send headers)"
    ),
):
    """SSE stream for real-time notifications.

    Uses query parameter token since browser EventSource doesn't support headers.
    """
    from core.security import decode_token

    user_id = decode_token(token, expected_type="access")

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
