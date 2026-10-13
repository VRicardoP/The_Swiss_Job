"""Mapeo de identidad usuario legacy -> perfil del core — A.SEAM matching.

El core identifica perfiles por su propio UUID (`profiles.id`) y solo permite
buscar por el (GET /v1/profiles/{id}); el vinculo con el legacy vive en el
CORE como `profiles.external_ref = str(users.id)` bajo `swissjob-shadow` —
que ES el consumer del BFF (CONTRATOS §3; el flip de Fase C lo consolida),
asi que esos perfiles son de NUESTRO tenant. Pero el BFF NO puede leer el
esquema del core (frontera estricta del plan §21: API /v1 + esquema propio)
y el /v1 no expone lookup por external_ref.

Por eso el vínculo se registra aquí, en una tabla local al BFF: es
transaccional, auditable y está disponible con Core caído. La entrega de
snapshots lo crea idempotentemente; sin fila, los clientes Core no emiten red.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class JobhuntProfileMap(Base):
    __tablename__ = "jobhunt_profile_map"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    core_profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # PK nombrada para coincidir con la migración inmutable.
        PrimaryKeyConstraint("user_id", name="pk_jobhunt_profile_map"),
        # Dos usuarios apuntando al mismo perfil core = error de operacion.
        UniqueConstraint("core_profile_id", name="uq_jobhunt_profile_map_core"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<JobhuntProfileMap {self.user_id} -> {self.core_profile_id}>"
