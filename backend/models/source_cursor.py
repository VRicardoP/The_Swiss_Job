"""SourceCursor — cursor incremental por fuente/scope.

Habilita el crawler INCREMENTAL: guardamos las identidades (URLs) de las ofertas
recientes ya vistas para parar de paginar en cuanto una fuente deja de traer
novedades (early-stop). Eje del sistema: **el volumen de peticiones depende del
número de ofertas NUEVAS, no del total** (ver a.txt / PLAN_STEALTH_SCRAPER_JOBUP).

La identidad usada es la `url` de la oferta (única en la tabla jobs); `cursor_type`
queda preparado para futuros cursores por timestamp/hash sin migrar de nuevo.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class SourceCursor(Base):
    """Estado incremental de una fuente para un scope (por defecto uno por fuente)."""

    __tablename__ = "source_cursors"
    __table_args__ = (
        UniqueConstraint("source_key", "scope_key", name="uq_source_cursor_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # Scope estable: por defecto "default" (un scope por fuente). Permite en el
    # futuro separar por query/location/idioma sin cambiar el esquema.
    scope_key: Mapped[str] = mapped_column(
        String(200), nullable=False, default="default"
    )
    # Tipo de identidad guardada: url | hash | timestamp. Hoy: url.
    cursor_type: Mapped[str] = mapped_column(String(20), nullable=False, default="url")
    # Ventana corta de identidades recientes (URLs) para el early-stop.
    recent_identities: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    # Marca de agua temporal (preparada para cursores por fecha; hoy informativa).
    high_watermark_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    bootstrap_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Métricas para presupuesto dinámico / observabilidad.
    avg_new_jobs_per_run: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    avg_pages_per_run: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    consecutive_empty_runs: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    consecutive_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_empty_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<SourceCursor {self.source_key}/{self.scope_key} "
            f"recent={len(self.recent_identities or [])}>"
        )
