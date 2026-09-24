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


def create_access_token(user_id: uuid.UUID, token_version: int = 0) -> str:
    """H6/T9: el access token lleva DENTRO la generación que lo autoriza.

    Un access token no tiene fila que revocar —sería una consulta por petición—
    y vive poco. Lo que sí se puede es invalidar todos los de un usuario a la
    vez subiendo su `token_version`: los emitidos antes dejan de casar.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "type": "access",
        "tv": int(token_version),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(
    user_id: uuid.UUID,
    *,
    jti: uuid.UUID | None = None,
    family_id: uuid.UUID | None = None,
) -> tuple[str, uuid.UUID, uuid.UUID]:
    """Devuelve `(token, jti, family_id)`.

    El `jti` es la identidad revocable del token y `family_id` la cadena de
    rotaciones que nace de un login. Sin `family_id` se abre una nueva (login);
    con ella, el token continúa la cadena (rotación en `/auth/refresh`).

    Devuelve los tres valores a propósito: quien emite el token es quien tiene
    que persistir su fila, y volver a decodificar el JWT para averiguar su
    propio `jti` sería pedirle a la firma que nos cuente lo que acabamos de
    escribir nosotros.
    """
    jti = jti or uuid.uuid4()
    family_id = family_id or uuid.uuid4()
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "type": "refresh",
        "jti": str(jti),
        "fam": str(family_id),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, jti, family_id


def decode_token_payload(token: str, expected_type: str = "access") -> dict:
    """El payload VALIDADO de un token: firma, tipo y sujeto comprobados.

    Sustituye al antiguo `decode_token`, que sólo devolvía el sujeto. Desde T9
    ningún llamador se conforma con eso —`get_current_user` mira `tv` y
    `/auth/refresh` mira `jti` y `fam`—, así que mantener las dos vistas dejaba
    una muerta.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError:
        raise credentials_exception
    if payload.get("type") != expected_type:
        raise credentials_exception
    try:
        uuid.UUID(str(payload.get("sub")))
    except (TypeError, ValueError):
        raise credentials_exception
    return payload


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    from models.user import User

    payload = decode_token_payload(token, expected_type="access")
    user_id = uuid.UUID(payload["sub"])

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
    # H6/T9: un token emitido antes del último corte de sesiones ya no vale.
    # Los tokens ANTERIORES a este cambio no traen `tv`; se leen como 0, que es
    # la generación inicial — no se echa a nadie por desplegar.
    if int(payload.get("tv", 0)) != int(user.token_version or 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
