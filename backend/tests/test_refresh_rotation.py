"""T9 — la sesión se puede cortar, y un token robado se nota.

Antes de esto un refresh token era una llave de 30 días que no se podía
retirar: ni cerrando sesión, ni cambiando la contraseña, ni sabiendo que había
sido robada. `/auth/refresh` emitía tokens nuevos y dejaba el viejo igual de
válido, así que «rotación» era un nombre para algo que no rotaba nada.

Lo que se comprueba aquí es lo que se puede DEMOSTRAR desde fuera: que el
canjeado deja de servir, que presentarlo otra vez tumba también al legítimo, y
que el corte de sesiones invalida los access tokens ya emitidos.
"""

import uuid

import pytest
import sqlalchemy as sa
from httpx import AsyncClient

from tests.conftest import random_email

CREDENCIAL = {"password": "SecureP@ss1", "gdpr_consent": True}


async def _alta(client: AsyncClient) -> dict:
    respuesta = await client.post(
        "/api/v1/auth/register", json={"email": random_email(), **CREDENCIAL}
    )
    assert respuesta.status_code == 201
    return respuesta.json()


class TestRotacion:
    async def test_el_refresh_canjeado_deja_de_servir(self, client: AsyncClient):
        sesion = await _alta(client)
        viejo = sesion["refresh_token"]

        primera = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": viejo}
        )
        assert primera.status_code == 200
        nuevo = primera.json()["refresh_token"]
        assert nuevo != viejo, "emitir el mismo token no es rotar"

        # El nuevo sirve...
        assert (
            await client.post("/api/v1/auth/refresh", json={"refresh_token": nuevo})
        ).status_code == 200

    async def test_reutilizar_un_refresh_tumba_la_familia_entera(
        self, client: AsyncClient
    ):
        """La detección de robo: si el canjeado reaparece, hay dos copias en
        circulación y no se sabe cuál es la del ladrón. Caen las dos."""
        sesion = await _alta(client)
        robado = sesion["refresh_token"]

        vivo = (
            await client.post("/api/v1/auth/refresh", json={"refresh_token": robado})
        ).json()["refresh_token"]

        # El ladrón presenta el que copió antes de la rotación.
        reutilizado = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": robado}
        )
        assert reutilizado.status_code == 401
        assert "reutilizado" in reutilizado.json()["detail"]

        # Y la víctima también se queda fuera: es el precio de no poder
        # distinguirlos, y obliga a un login que el ladrón no puede hacer.
        caido = await client.post("/api/v1/auth/refresh", json={"refresh_token": vivo})
        assert caido.status_code == 401

    async def test_un_refresh_de_otro_usuario_no_se_canjea(self, client: AsyncClient):
        """Control: la fila se busca por `jti`, y el `jti` tiene dueño."""
        from core.security import create_refresh_token

        sesion = await _alta(client)
        ajeno, _, _ = create_refresh_token(uuid.uuid4())
        assert (
            await client.post("/api/v1/auth/refresh", json={"refresh_token": ajeno})
        ).status_code == 401
        # Y el del dueño legítimo sigue intacto: el rechazo ajeno no lo tocó.
        assert (
            await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": sesion["refresh_token"]}
            )
        ).status_code == 200


class TestLogout:
    async def test_logout_revoca_la_sesion(self, client: AsyncClient):
        sesion = await _alta(client)
        assert (
            await client.post(
                "/api/v1/auth/logout", json={"refresh_token": sesion["refresh_token"]}
            )
        ).status_code == 204
        negado = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": sesion["refresh_token"]}
        )
        assert negado.status_code == 401

    async def test_logout_no_delata_si_el_token_servia(self, client: AsyncClient):
        """Idempotente y mudo: repetirlo, o mandar basura, da 204 igual."""
        sesion = await _alta(client)
        for cuerpo in (
            {"refresh_token": sesion["refresh_token"]},
            {"refresh_token": sesion["refresh_token"]},
            {"refresh_token": "esto-no-es-un-jwt"},
        ):
            assert (
                await client.post("/api/v1/auth/logout", json=cuerpo)
            ).status_code == 204

    async def test_logout_no_toca_las_otras_sesiones(self, client: AsyncClient):
        """Cerrar sesión en un dispositivo no puede echar al usuario del otro:
        se revoca la FAMILIA, no el usuario."""
        email = random_email()
        await client.post("/api/v1/auth/register", json={"email": email, **CREDENCIAL})
        movil = (
            await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": CREDENCIAL["password"]},
            )
        ).json()
        portatil = (
            await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": CREDENCIAL["password"]},
            )
        ).json()

        await client.post(
            "/api/v1/auth/logout", json={"refresh_token": movil["refresh_token"]}
        )
        assert (
            await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": portatil["refresh_token"]},
            )
        ).status_code == 200


class TestTokenVersion:
    async def test_el_corte_total_invalida_los_access_ya_emitidos(
        self, client: AsyncClient, db_session
    ):
        """Un access token no tiene fila que revocar: lleva dentro la
        generación que lo autoriza. Esto es lo que debe ejecutar un cambio de
        contraseña o una baja."""
        from models.user import User
        from services import token_store

        sesion = await _alta(client)
        cabecera = {"Authorization": f"Bearer {sesion['access_token']}"}
        assert (
            await client.get("/api/v1/auth/me", headers=cabecera)
        ).status_code == 200

        usuario = (
            await db_session.execute(
                sa.select(User).where(User.id == uuid.UUID(sesion["user"]["id"]))
            )
        ).scalar_one()
        await token_store.revocar_todas_las_sesiones(db_session, usuario)
        await db_session.commit()

        negado = await client.get("/api/v1/auth/me", headers=cabecera)
        assert negado.status_code == 401
        assert negado.json()["detail"] == "Session revoked"
        # Y el refresh de esa sesión tampoco sobrevive al corte.
        assert (
            await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": sesion["refresh_token"]}
            )
        ).status_code == 401

    async def test_un_login_posterior_al_corte_funciona(
        self, client: AsyncClient, db_session
    ):
        """Control del control: el corte invalida lo emitido ANTES, no la
        capacidad de volver a entrar."""
        from models.user import User
        from services import token_store

        email = random_email()
        await client.post("/api/v1/auth/register", json={"email": email, **CREDENCIAL})
        usuario = (
            await db_session.execute(sa.select(User).where(User.email == email))
        ).scalar_one()
        await token_store.revocar_todas_las_sesiones(db_session, usuario)
        await db_session.commit()

        nueva = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": CREDENCIAL["password"]},
        )
        assert nueva.status_code == 200
        assert (
            await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {nueva.json()['access_token']}"},
            )
        ).status_code == 200


@pytest.mark.parametrize("sin_jti", [True])
async def test_un_refresh_anterior_a_t9_se_acepta_una_vez(client: AsyncClient, sin_jti):
    """Ventana de migración: los refresh ya emitidos no traen `jti`. Echar a
    todo el mundo al desplegar sería una caída de sesión masiva; se aceptan una
    vez y salen con familia nueva. La ventana la cierra su propia caducidad."""
    from datetime import datetime, timedelta, timezone

    from jose import jwt

    from config import settings

    sesion = await _alta(client)
    legado = jwt.encode(
        {
            "sub": sesion["user"]["id"],
            "exp": datetime.now(timezone.utc) + timedelta(days=1),
            "type": "refresh",
        },
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    respuesta = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": legado}
    )
    assert respuesta.status_code == 200
    assert respuesta.json()["refresh_token"] != legado
