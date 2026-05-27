"""Modelos para filtros de usuario y sugerencias de patrones.

JobFilter: filtros aprobados y activos que excluyen jobs del matching.
PatternSuggestion: sugerencias generadas automáticamente pendientes de aprobación.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class JobFilter(Base):
    """Filtro de exclusión aprobado por el usuario."""

    __tablename__ = "job_filters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # title_contains | tag_contains
    filter_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # El patrón a buscar (case-insensitive LIKE para title_contains)
    pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Cuántas veces ha filtrado un job en los últimos runs
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # auto = generado por análisis | manual = creado por el usuario
    source: Mapped[str] = mapped_column(String(20), default="auto")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PatternSuggestion(Base):
    """Sugerencia de patrón de exclusión pendiente de aprobación del usuario."""

    __tablename__ = "pattern_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # title_pattern | tag_category
    suggestion_type: Mapped[str] = mapped_column(String(30), nullable=False)
    pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Puntuación de relevancia [0.0, 1.0]: frecuencia × tasa de rechazo
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # Lista de {"title": str, "company": str} de ejemplos de jobs rechazados
    sample_jobs: Mapped[list] = mapped_column(JSONB, default=list)
    # Jobs rechazados que contienen este patrón
    affected_count: Mapped[int] = mapped_column(Integer, default=0)
    # pending | approved | rejected
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
