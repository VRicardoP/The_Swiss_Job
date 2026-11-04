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
        self, client: AsyncClient, monkeypatch
    ):
        """La detección de robo: si el canjeado reaparece, hay dos copias en
        circulación y no se sabe cuál es la del ladrón. Caen las dos.

        Desde A-12 esto vale FUERA de la ventana de gracia; dentro de ella, un
        re-canje es la carrera normal entre pestañas y se perdona (ver
        `TestVentanaDeGracia`). El test fija el caso del robo desactivando la
        gracia en vez de esperar, porque lo que aquí se prueba es la política de
        revocación y no el reloj.
        """
        from config import settings

        monkeypatch.setattr(settings, "REFRESH_GRACE_SECONDS", 0)

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


async def test_un_refresh_SIN_JTI_se_rechaza(client: AsyncClient):
    """A-43: la rama de la ventana de migración de T9 se retiró.

    Esos tokens (emitidos antes de T9, sin `jti`) se aceptaban una vez para no
    echar a nadie al desplegar. Dos motivos para retirarla:

    - Su ventana CADUCÓ el 2026-10-02: `REFRESH_TOKEN_EXPIRE_DAYS` son 7 días
      desde el despliegue de T9, así que no queda ninguno vivo.
    - Y NO implementaba la garantía que su propio docstring describía. El test
      que la cubría —éste, antes de reescribirse— comprobaba que se ACEPTA, no
      que la segunda vez se rechace; y no se rechazaba: sin fila no había nada
      que marcar como consumido, así que uno de esos tokens se podía presentar
      cuantas veces se quisiera hasta su `exp`, acuñando familia nueva cada vez.
      Ni `logout` ni `revocar_familia` lo alcanzaban, precisamente por no tener
      fila. El nombre del test afirmaba lo que el test no probaba.
    """
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
    assert respuesta.status_code == 401

    # Y la sesión legítima del usuario NO se toca: un token sin identidad
    # revocable no pertenece a ninguna familia, así que no hay nada que revocar.
    sigue = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": sesion["refresh_token"]}
    )
    assert sigue.status_code == 200


@pytest.mark.asyncio
async def test_la_purga_borra_lo_caducado_y_conserva_la_memoria(db_session):
    """DT-148 A3: la purga por caducidad existía y no la llamaba nadie.

    Lo que debe borrar: filas cuya caducidad quedó MUY atrás (hay margen: una
    recién caducada todavía sirve para responder «reutilizado»). Lo que NO
    debe borrar: lo vigente, ni siquiera revocado — una fila revocada es la
    memoria que distingue el robo de un token desconocido.
    """
    from datetime import datetime, timedelta, timezone

    from models.refresh_token import RefreshToken
    from services.token_store import purgar_caducados

    ahora = datetime.now(timezone.utc)
    familia = uuid.uuid4()
    caducado = uuid.uuid4()
    reciente = uuid.uuid4()
    vigente_revocado = uuid.uuid4()
    usuario = await _alta_directa(db_session)
    for jti, expira, revocado in (
        (caducado, ahora - timedelta(days=30), None),
        (reciente, ahora - timedelta(days=1), None),
        (vigente_revocado, ahora + timedelta(days=5), ahora),
    ):
        db_session.add(
            RefreshToken(
                jti=jti,
                family_id=familia,
                user_id=usuario,
                expires_at=expira,
                revoked_at=revocado,
            )
        )
    await db_session.commit()

    borrados = await purgar_caducados(db_session)
    await db_session.commit()

    quedan = set(
        (
            await db_session.execute(
                sa.select(RefreshToken.jti).where(RefreshToken.family_id == familia)
            )
        )
        .scalars()
        .all()
    )
    assert borrados == 1
    assert quedan == {reciente, vigente_revocado}


async def _alta_directa(db) -> uuid.UUID:
    """Usuario mínimo para colgar filas de refresh (sin pasar por HTTP)."""
    from core.security import hash_password
    from models.user import User

    uid = uuid.uuid4()
    db.add(
        User(
            id=uid,
            email=random_email(),
            hashed_password=hash_password("SecureP@ss1"),
            gdpr_consent=True,
        )
    )
    await db.commit()
    return uid


class TestVentanaDeGracia:
    """A-12: la detección de robo se disparaba con una carrera NORMAL.

    El canje único es correcto frente a carreras, pero ESA es justamente la
    señal que revoca la familia, y el frontend no la acompaña: serializa los
    refresh con una promesa compartida POR PESTAÑA mientras el token vive en
    `localStorage`, que es compartido. Dos pestañas con el access caducado
    refrescan en paralelo con el mismo token: una gana, la otra presenta uno ya
    canjeado, y caían LAS DOS. Lo peor no era el deslogueo sino que envenenaba
    la señal: el log de robo dejaba de significar robo.

    Control negativo: con `REFRESH_GRACE_SECONDS = 0` los dos primeros casos
    fallan (vuelve la revocación de familia), que es el comportamiento anterior.
    """

    async def test_dos_pestanas_con_el_mismo_refresh_sobreviven(
        self, client: AsyncClient
    ):
        sesion = await _alta(client)
        rt = sesion["refresh_token"]

        # Pestaña A gana la carrera.
        a = await client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
        assert a.status_code == 200

        # Pestaña B presenta el MISMO token, un instante después.
        b = await client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
        assert b.status_code == 200, "la carrera normal no puede cerrar la sesión"
        assert b.json()["refresh_token"] not in (rt, a.json()["refresh_token"])

    async def test_el_token_que_B_recibe_SIRVE(self, client: AsyncClient):
        """Conceder un 200 con un token inservible sería peor que el 401."""
        sesion = await _alta(client)
        rt = sesion["refresh_token"]

        await client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
        b = await client.post("/api/v1/auth/refresh", json={"refresh_token": rt})

        siguiente = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": b.json()["refresh_token"]},
        )
        assert siguiente.status_code == 200

    async def test_FUERA_de_la_ventana_sigue_cayendo_la_familia(
        self, client: AsyncClient, monkeypatch
    ):
        """La gracia no desarma la detección: pasada la ventana, un token
        reaparecido sigue siendo la única señal de robo que ve un servidor sin
        estado."""
        from config import settings

        sesion = await _alta(client)
        rt = sesion["refresh_token"]

        a = await client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
        assert a.status_code == 200

        monkeypatch.setattr(settings, "REFRESH_GRACE_SECONDS", 0)
        b = await client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
        assert b.status_code == 401

        # Y cae la familia ENTERA: el token que A obtuvo tampoco sirve ya.
        tras = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": a.json()["refresh_token"]},
        )
        assert tras.status_code == 401

    async def test_un_token_REVOCADO_por_logout_no_recibe_gracia(
        self, client: AsyncClient
    ):
        """`replaced_by` es el discriminador: un logout deja `revoked_at` puesto
        y `replaced_by` NULO. Revocar es una orden, no una carrera, y perdonarla
        reabriría justo el agujero que T9 cerró."""
        sesion = await _alta(client)
        rt = sesion["refresh_token"]

        cierre = await client.post("/api/v1/auth/logout", json={"refresh_token": rt})
        assert cierre.status_code == 204

        reintento = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": rt}
        )
        assert reintento.status_code == 401

    async def test_la_cadena_sigue_teniendo_UN_solo_token_vivo(
        self, client: AsyncClient, db_session
    ):
        """La gracia continúa la cadena en vez de emitir un hermano, para
        conservar el invariante que hace útil la detección: a lo sumo un refresh
        vivo por familia. Dos hermanos válidos serían el estado que el detector
        persigue."""
        import sqlalchemy as sa

        from models.refresh_token import RefreshToken

        sesion = await _alta(client)
        rt = sesion["refresh_token"]
        await client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
        await client.post("/api/v1/auth/refresh", json={"refresh_token": rt})

        vivos = (
            await db_session.execute(
                sa.select(sa.func.count())
                .select_from(RefreshToken)
                .where(RefreshToken.revoked_at.is_(None))
            )
        ).scalar()
        assert vivos == 1, f"debería quedar exactamente uno vivo, hay {vivos}"
