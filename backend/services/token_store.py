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


async def _en_ventana_de_gracia(db: AsyncSession, fila, ahora: datetime) -> bool:
    """¿Este re-canje es la carrera normal entre pestañas, y no un robo?

    A-12. El canje único es correcto frente a carreras, pero ESA es justamente
    la señal que revoca la familia, y el frontend no la acompaña: serializa los
    refresh con una promesa compartida POR PESTAÑA, mientras el token vive en
    `localStorage`, que es compartido. Dos pestañas con el access caducado
    refrescan en paralelo con el MISMO token: una gana, la otra presenta uno ya
    canjeado y caen las dos. Y lo peor no es el deslogueo: es que **envenena la
    señal** — el log de robo deja de significar robo.

    Tres condiciones, y las tres hacen falta:

    1. `replaced_by` no es nulo. Es el discriminador que separa «se rotó» de
       «se revocó»: un logout o una caída de familia dejan `revoked_at` puesto y
       `replaced_by` NULO, y a esos NUNCA se les da gracia — revocar es una
       orden, no una carrera.
    2. El canje fue hace menos de `REFRESH_GRACE_SECONDS`. Fuera de la ventana
       un token reaparecido sigue siendo la única señal de robo que ve un
       servidor sin estado, y se trata como tal.
    3. El sucesor sigue SANO: existe, no está revocado y no ha caducado. Si la
       punta de la cadena ya murió, la familia está en un estado que la gracia
       no debe perdonar.

    Lo que esto concede a un atacante, dicho explícitamente: con un refresh
    robado puede obtener sesión durante los segundos de la ventana sin disparar
    la revocación. Es el intercambio que recomienda el BCP de OAuth 2.1, y por
    eso la ventana es corta y configurable; `REFRESH_GRACE_SECONDS=0` recupera
    el comportamiento estricto. A cambio, la señal de robo vuelve a significar
    robo, que es lo que la hace útil.
    """
    if fila.replaced_by is None or settings.REFRESH_GRACE_SECONDS <= 0:
        return False
    if fila.revoked_at is None:
        return False
    canjeado_hace = (ahora - fila.revoked_at).total_seconds()
    if canjeado_hace > settings.REFRESH_GRACE_SECONDS:
        return False

    sucesor = (
        await db.execute(
            sa.select(RefreshToken).where(RefreshToken.jti == fila.replaced_by)
        )
    ).scalar_one_or_none()
    return (
        sucesor is not None
        and sucesor.revoked_at is None
        and sucesor.expires_at > ahora
    )


async def _continuar_cadena(
    db: AsyncSession, user, jti_punta: uuid.UUID, ahora: datetime
) -> tuple[str, str]:
    """Emite el siguiente eslabón desde la PUNTA de la cadena.

    Se continúa la cadena en vez de emitir un hermano porque eso conserva el
    invariante que hace útil la detección de robo: a lo sumo un refresh vivo por
    familia. Un hermano dejaría dos tokens válidos a la vez, que es exactamente
    el estado que el detector persigue.

    La punta se relee con `FOR UPDATE` —no se reusa la fila ya leída— porque
    entre la comprobación de la gracia y esta escritura puede haberla canjeado
    otro. Si al bloquearla ya no está sana, se cae al camino estricto.
    """
    punta = (
        await db.execute(
            sa.select(RefreshToken)
            .where(RefreshToken.jti == jti_punta)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if punta is None or punta.revoked_at is not None or punta.expires_at <= ahora:
        await revocar_familia(db, punta.family_id if punta else user.id)
        raise RefreshRechazado(
            "refresh token reutilizado: sesión revocada", familia_revocada=True
        )

    nuevo_refresh, nuevo_jti, _ = create_refresh_token(
        user.id, family_id=punta.family_id
    )
    punta.replaced_by = nuevo_jti
    punta.revoked_at = ahora
    db.add(
        RefreshToken(
            jti=nuevo_jti,
            user_id=user.id,
            family_id=punta.family_id,
            expires_at=_caducidad(),
        )
    )
    return create_access_token(user.id, user.token_version or 0), nuevo_refresh


async def rotar(db: AsyncSession, user, payload: dict) -> tuple[str, str]:
    """Canjea el refresh descrito por `payload` por uno nuevo de su familia.

    Desenlaces:
    - token sin `jti`: `RefreshRechazado`. Eran los emitidos ANTES de T9 y se
      aceptaban una vez para no echar a nadie al desplegar; la rama se retiró el
      2026-11-04 (A-43) porque su ventana caducó el 2026-10-02
      (`REFRESH_TOKEN_EXPIRE_DAYS` = 7 desde el despliegue de T9) y, peor, NO
      implementaba la garantía que decía: no marcaba nada como consumido, así
      que uno de esos tokens se podía presentar cuantas veces se quisiera hasta
      su `exp`, acuñando familia nueva cada vez, y ni `logout` ni
      `revocar_familia` lo alcanzaban porque no tenía fila.
    - token ya canjeado DENTRO de la ventana de gracia y con el sucesor sano:
      se continúa la cadena sin revocar nada. Es la carrera normal entre
      pestañas (A-12), no un robo.
    - token desconocido, revocado, o canjeado FUERA de la gracia:
      `RefreshRechazado`. En el último caso se revoca la familia entera antes de
      rechazar.
    - token vivo: se marca canjeado y se emite el siguiente.
    """
    bruto = payload.get("jti")
    if bruto is None:
        raise RefreshRechazado("refresh token sin identidad revocable")
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
        if await _en_ventana_de_gracia(db, fila, ahora):
            # Carrera normal entre pestañas, no robo: se continúa la cadena
            # desde su PUNTA en vez de revocar. Ver `_en_ventana_de_gracia`.
            return await _continuar_cadena(db, user, fila.replaced_by, ahora)
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
