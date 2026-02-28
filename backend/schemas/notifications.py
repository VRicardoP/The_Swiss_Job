"""Pydantic schemas for notification endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationResponse(BaseModel):
    """Single notification."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    title: str
    body: str
    data: dict | None = None
    is_read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    """Paginated notification list with unread count."""

    data: list[NotificationResponse]
    total: int
    unread_count: int
