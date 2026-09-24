import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.rate_limit import limiter
from core.security import (
    decode_token_payload,
    get_current_user,
    hash_password,
    needs_rehash,
    verify_password,
)
from database import get_db
from models.user import User
from services import token_store

from models.user_profile import UserProfile
from schemas.auth import (
    AuthResponse,
    TokenRefresh,
    UserLogin,
    UserRegister,
    UserResponse,
)

# Hash señuelo contra el que se verifica cuando el correo no existe, para que
# el login tarde lo mismo exista la cuenta o no. Se calcula UNA vez al importar:
# hacerlo por petición añadiría su propio bcrypt y doblaría el tiempo.
_HASH_SENUELO = hash_password(uuid.uuid4().hex)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
async def register(
    request: Request, body: UserRegister, db: AsyncSession = Depends(get_db)
):
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    now = datetime.now(timezone.utc)
    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        gdpr_consent=True,
        gdpr_consent_at=now,
        last_login=now,
    )
    db.add(user)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    profile = UserProfile(user_id=user.id)
    db.add(profile)

    access_token, refresh_token = await token_store.emitir_sesion(db, user)
    await db.commit()
    await db.refresh(user)

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=AuthResponse)
@limiter.limit("5/minute")
async def login(request: Request, body: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # H6/T8 — tres oráculos de enumeración, los tres cerrados aquí:
    #
    # 1. Salir con 401 ANTES de verificar la contraseña hacía que un correo
    #    inexistente respondiera mucho más rápido que uno real: el tiempo
    #    delataba qué cuentas existen. Ahora SIEMPRE se calcula un bcrypt,
    #    contra un hash señuelo si el usuario no existe.
    # 2. `is_active` se comprobaba ANTES que la contraseña, así que un 403
    #    revelaba que la cuenta existe sin saber su contraseña. Ahora va
    #    después: sólo quien acierta la contraseña se entera de que está
    #    desactivada, que es a quien le sirve saberlo.
    # 3. El mensaje es el mismo para «no existe» y «contraseña incorrecta».
    almacenado = user.hashed_password if user is not None else _HASH_SENUELO
    contrasena_valida = verify_password(body.password, almacenado)
    if user is None or not contrasena_valida:
        raise invalid_credentials

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    # Migración transparente del esquema de hash: quien entra con una
    # contraseña guardada al estilo antiguo (bcrypt truncado a 72 bytes) sale
    # con ella re-hasheada. Sin esto, cambiar el esquema obligaría a resetear
    # la contraseña de todo el mundo.
    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(body.password)

    user.last_login = datetime.now(timezone.utc)
    access_token, refresh_token = await token_store.emitir_sesion(db, user)
    await db.commit()

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/refresh", response_model=AuthResponse)
@limiter.limit("10/minute")
async def refresh(
    request: Request, body: TokenRefresh, db: AsyncSession = Depends(get_db)
):
    payload = decode_token_payload(body.refresh_token, expected_type="refresh")
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

    try:
        access_token, refresh_token = await token_store.rotar(db, user, payload)
    except token_store.RefreshRechazado as rechazo:
        # La revocación de la familia hay que CONFIRMARLA aunque la respuesta
        # sea un error: si se fuera en rollback, el ladrón podría seguir
        # canjeando y la detección no habría servido de nada.
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(rechazo),
            headers={"WWW-Authenticate": "Bearer"},
        ) from rechazo
    await db.commit()

    return AuthResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserResponse.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/minute")
async def logout(
    request: Request, body: TokenRefresh, db: AsyncSession = Depends(get_db)
):
    """Cierra la sesión de ESTE dispositivo revocando su familia.

    No exige access token a propósito: cerrar sesión tiene que funcionar
    justamente cuando el access ya caducó, que es cuando el usuario lleva rato
    sin tocar la aplicación. Presentar un refresh válido ya demuestra que la
    sesión es suya, y lo único que se consigue es destruirla.

    Idempotente y mudo: un token ilegible, caducado o ya revocado devuelve 204
    igual. Un logout que contesta 401 le cuenta a quien lo prueba si el token
    servía; y ante un fallo de cierre de sesión el cliente no tiene nada mejor
    que hacer que borrar sus tokens, que es lo que va a hacer de todas formas.
    """
    try:
        payload = decode_token_payload(body.refresh_token, expected_type="refresh")
        familia = payload.get("fam")
        if familia:
            await token_store.revocar_familia(db, uuid.UUID(str(familia)))
            await db.commit()
    except (HTTPException, ValueError, TypeError):
        pass
    return None


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)
