"""Salud de cosecha por fuente (V.0).

Separada a propósito de `SourceCompliance`: aquella responde "¿tengo PERMISO
para cosechar esta fuente?" (robots, ToS, kill-switch), y esta responde
"¿la fuente FUNCIONA y está trayendo ofertas?". Son dos razones de cambio
distintas, y `source_compliance` solo cubre scrapers mientras que la salud
aplica igual a providers de API.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

# Veredictos de un run (los produce `utils.fetch_diagnostics.classify`).
OUTCOME_OK = "ok"
OUTCOME_EMPTY = "empty"
OUTCOME_ERROR = "error"


class SourceHealth(Base):
    """Último resultado conocido de cosecha de una fuente + rachas."""

    __tablename__ = "source_health"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_key: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )

    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # `ok` | `empty` | `error` del ÚLTIMO intento.
    last_outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Último intento que trajo al menos una oferta.
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_jobs_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # "HTTP 404 en https://..." — el porqué, no solo que falló.
    last_error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Rachas: son la señal de alerta. `consecutive_empty` cuenta runs SIN error
    # y sin ofertas (una fuente que responde pero lleva días seca); NO se
    # mezclan con los errores, porque exigen acciones distintas.
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_empty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
