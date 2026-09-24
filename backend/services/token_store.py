"""Rotación y revocación de refresh tokens — H6/T9.

Toda la política de sesiones vive aquí; el router sólo la invoca. Tres
operaciones y una regla:

- `emitir_sesion`: nace una familia (login/registro).
- `rotar`: se canjea un refresh por otro de la MISMA familia.
- `revocar_familia`: se corta la cadena entera (logout, o robo detectado).

La regla: **un refresh se canjea UNA vez**. Si vuelve a presentarse uno ya
canjeado hay dos copias circulando y no se puede distinguir a la víctima del
ladrón, así que caen las dos. Es la única señal de robo que un servidor sin
estado llega a ver.

Lo que NO hace, a propósito: no borra filas al revocar. Una fila revocada es
la memoria que permite detectar la reutilización; borrarla haría que el token
robado pasara de «reutilizado» a «desconocido», que es un estado más débil.
La purga va por caducidad (`purgar_caducados`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.security import create_access_token, create_refresh_token
from models.refresh_token import RefreshToken


class RefreshRechazado(Exception):
    """El refresh presentado no puede canjearse. `familia_revocada` distingue
    el robo detectado (hubo que cortar una sesión viva) de un token que
    simplemente ya no vale."""

    def __init__(self, motivo: str, *, familia_revocada: bool = False):
        super().__init__(motivo)
        self.familia_revocada = familia_revocada


def _caducidad() -> datetime:
    return datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )


async def emitir_sesion(db: AsyncSession, user) -> tuple[str, str]:
    """Abre una familia nueva. Devuelve `(access, refresh)`.

    No hace commit: quien llama decide la transacción (el login además escribe
    `last_login` y a veces el re-hash de la contraseña, y todo eso tiene que
    entrar o no entrar junto).
    """
    refresh, jti, family_id = create_refresh_token(user.id)
    db.add(
        RefreshToken(
            jti=jti,
            user_id=user.id,
            family_id=family_id,
            expires_at=_caducidad(),
        )
    )
    return create_access_token(user.id, user.token_version or 0), refresh


async def rotar(db: AsyncSession, user, payload: dict) -> tuple[str, str]:
    """Canjea el refresh descrito por `payload` por uno nuevo de su familia.

    Tres desenlaces:
    - token sin `jti` (emitido antes de T9): se acepta UNA vez y se le abre
      familia nueva, para no echar a quien ya tenía sesión al desplegar. La
      ventana se cierra sola al caducar los tokens viejos.
    - token desconocido o ya canjeado: `RefreshRechazado`. En el segundo caso
      se revoca la familia entera antes de rechazar.
    - token vivo: se marca canjeado y se emite el siguiente.
    """
    bruto = payload.get("jti")
    if bruto is None:
        return await emitir_sesion(db, user)
    try:
        jti = uuid.UUID(str(bruto))
    except (TypeError, ValueError):
        raise RefreshRechazado("refresh token ilegible")

    fila = (
        await db.execute(
            sa.select(RefreshToken).where(RefreshToken.jti == jti).with_for_update()
        )
    ).scalar_one_or_none()
    if fila is None or fila.user_id != user.id:
        # Firmado por nosotros pero sin fila: o se revocó y purgó por caducidad,
        # o el `SECRET_KEY` se reutilizó entre entornos. En ninguno de los dos
        # casos se puede emitir una sesión nueva.
        raise RefreshRechazado("refresh token desconocido")

    ahora = datetime.now(timezone.utc)
    if fila.replaced_by is not None or fila.revoked_at is not None:
        await revocar_familia(db, fila.family_id)
        raise RefreshRechazado(
            "refresh token reutilizado: sesión revocada", familia_revocada=True
        )
    if fila.expires_at <= ahora:
        raise RefreshRechazado("refresh token caducado")

    nuevo_refresh, nuevo_jti, _ = create_refresh_token(
        user.id, family_id=fila.family_id
    )
    fila.replaced_by = nuevo_jti
    fila.revoked_at = ahora
    db.add(
        RefreshToken(
            jti=nuevo_jti,
            user_id=user.id,
            family_id=fila.family_id,
            expires_at=_caducidad(),
        )
    )
    return create_access_token(user.id, user.token_version or 0), nuevo_refresh


async def revocar_familia(db: AsyncSession, family_id: uuid.UUID) -> int:
    """Corta la cadena entera. Devuelve cuántos tokens vivos había."""
    resultado = await db.execute(
        sa.update(RefreshToken)
        .where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    return resultado.rowcount or 0


async def revocar_todas_las_sesiones(db: AsyncSession, user) -> None:
    """Corte total: refrescos revocados Y access tokens invalidados.

    Es lo que debe llamar un cambio de contraseña o una baja de cuenta. Hoy no
    hay endpoint que haga ninguna de las dos cosas —por eso el corte se prueba
    directamente—, pero el mecanismo tiene que existir ANTES que el endpoint:
    añadirlo después significa que durante un tiempo cambiar la contraseña no
    echaba a nadie, que es justo el agujero que T9 cierra.
    """
    await db.execute(
        sa.update(RefreshToken)
        .where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    user.token_version = int(user.token_version or 0) + 1


async def purgar_caducados(db: AsyncSession) -> int:
    """Borra lo que ya no puede presentarse. Se conserva un margen: una fila
    recién caducada todavía sirve para responder «reutilizado» en vez de
    «desconocido» si el ladrón llega tarde."""
    corte = datetime.now(timezone.utc) - timedelta(days=7)
    resultado = await db.execute(
        sa.delete(RefreshToken).where(RefreshToken.expires_at < corte)
    )
    return resultado.rowcount or 0
