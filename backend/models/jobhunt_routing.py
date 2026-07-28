"""Tabla de routing por perfil+capacidad — A.SEAM (plan §15bis).

El routing vive EN el BFF (no en el core ni en una env var): debe ser
dinamico, transaccional, auditable y estar disponible aunque el core este
caido. Columnas EXACTAS del plan §15bis:
    jobhunt_routing(consumer_id, profile_id, capability,
                    mode[local|shadow|core_read|core_primary|rollback_pending],
                    revision, updated_by, updated_at)

Semantica:
- Ausencia de fila => modo 'local' (default seguro; todo arranca en local).
- `profile_id` comodin (PROFILE_WILDCARD, UUID nulo) => la fila aplica a todo
  el consumer: capacidades sin contexto de perfil (catalogo anonimo) y
  default consumer-wide. La PK compuesta exige NOT NULL, de ahi el centinela
  en lugar de NULL.
- `revision` se incrementa en cada cambio (auditoria + optimista).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

# Modos del plan §15bis (matriz de escritor por estado).
ROUTING_MODES = (
    "local",
    "shadow",
    "core_read",
    "core_primary",
    "rollback_pending",
)

# Perfil comodin: aplica a TODO el consumer (endpoints sin perfil, defaults).
PROFILE_WILDCARD = uuid.UUID(int=0)

# Identidad de ESTE BFF como consumer del core (cada BFF tiene su tabla).
CONSUMER_SWISSJOB = "swissjob"


class JobhuntRouting(Base):
    __tablename__ = "jobhunt_routing"

    consumer_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=CONSUMER_SWISSJOB
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=PROFILE_WILDCARD
    )
    capability: Mapped[str] = mapped_column(String(32), primary_key=True)
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="local"
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # PK compuesta NOMBRADA: mismo nombre que la migracion b7d1a5c9e402
        # (sin esto, create_all genera 'jobhunt_routing_pkey' y el esquema
        # del modelo diverge del real; lo vigila tests/test_migration_smoke.py).
        PrimaryKeyConstraint(
            "consumer_id", "profile_id", "capability", name="pk_jobhunt_routing"
        ),
        CheckConstraint(
            "mode IN ('local','shadow','core_read','core_primary','rollback_pending')",
            name="ck_jobhunt_routing_mode",
        ),
        # Indice requerido por A.SEAM: resolucion por (profile_id, capability).
        Index("ix_jobhunt_routing_profile_capability", "profile_id", "capability"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<JobhuntRouting {self.consumer_id}/{self.profile_id}/"
            f"{self.capability} -> {self.mode} (rev {self.revision})>"
        )
