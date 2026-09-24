import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db


# H6/T8 — bcrypt sólo mira los primeros 72 BYTES y los demás los descarta EN
# SILENCIO (medido con bcrypt 4.3.0: una contraseña de 100 caracteres valida
# contra sus primeros 72). Con `max_length=128` en el registro eso significaba
# que un tercio de una contraseña larga no protegía nada, y en el login no había
# tope alguno. Se pre-digiere con SHA-256 y se pasa en base64: 44 bytes fijos,
# sin truncado posible y sin límite de longitud para el usuario.
_PREFIJO_SHA256 = "sha256$"


def _material(plain_password: str) -> bytes:
    """Lo que ve bcrypt: 44 bytes, pase lo que pase por arriba."""
    return base64.b64encode(hashlib.sha256(plain_password.encode()).digest())


def hash_password(plain_password: str) -> str:
    hashed = bcrypt.hashpw(_material(plain_password), bcrypt.gensalt()).decode()
    return _PREFIJO_SHA256 + hashed


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Acepta el esquema nuevo y el antiguo; el llamante decide si re-hashea.

    Sin esta compatibilidad, cambiar el esquema dejaría fuera a todo el que ya
    tuviera cuenta. El esquema antiguo se distingue por NO llevar prefijo.
    """
    if hashed_password.startswith(_PREFIJO_SHA256):
        return bcrypt.checkpw(
            _material(plain_password), hashed_password[len(_PREFIJO_SHA256) :].encode()
        )
    # Legado: bcrypt directo sobre la contraseña (truncada a 72 bytes).
    return bcrypt.checkpw(plain_password.encode()[:72], hashed_password.encode())


def needs_rehash(hashed_password: str) -> bool:
    """True si la contraseña guardada usa todavía el esquema truncable."""
    return not hashed_password.startswith(_PREFIJO_SHA256)


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def create_access_token(user_id: uuid.UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": str(user_id), "exp": expire, "type": "access"}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: uuid.UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    payload = {"sub": str(user_id), "exp": expire, "type": "refresh"}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str, expected_type: str = "access") -> uuid.UUID:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id_str: str | None = payload.get("sub")
        token_type: str | None = payload.get("type")

        if user_id_str is None or token_type != expected_type:
            raise credentials_exception

        return uuid.UUID(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exception


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    from models.user import User

    user_id = decode_token(token, expected_type="access")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )
    return user
