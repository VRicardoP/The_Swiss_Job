"""Refresh tokens con identidad propia — H6/T9.

Un JWT es una afirmación FIRMADA, no una sesión: hasta su expiración vale
aunque el usuario cierre sesión, cambie la contraseña o le roben el token. El
refresh vivía 30 días, así que un token filtrado era 30 días de acceso y no
había forma de cortarlo salvo rotar `SECRET_KEY` y echar a todo el mundo.

Esta tabla le da al refresh un `jti` que SÍ se puede revocar, y una FAMILIA:
la cadena de rotaciones que nace de un login. Si alguien presenta un `jti` que
ya se canjeó, hay dos copias del mismo token en circulación —la legítima y la
robada, y no se puede saber cuál es cuál—, así que se revoca la familia entera
y ambas partes tienen que volver a autenticarse. Es la detección de robo por
reutilización (OAuth 2.1 §4.14.2), y es el único momento en que un servidor
sin estado puede enterarse de que hubo un robo.

Se guarda el `jti`, NUNCA el token: quien lea la tabla no obtiene credenciales.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    # El `jti` del JWT es la clave: la fila ES el token, sin guardarlo.
    jti: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Cadena de rotaciones nacida de UN login. Se revoca entera o nada.
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # El `jti` que lo sustituyó. Presente = ya canjeado: volver a presentarlo
    # es la señal de robo, y por eso se distingue de `revoked_at` a secas.
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Revocar una familia y purgar lo caducado son las dos únicas lecturas.
        Index("ix_refresh_tokens_family", "family_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )
