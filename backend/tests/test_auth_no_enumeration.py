"""H6/T8 — el login deja de delatar qué cuentas existen, y la contraseña
deja de truncarse en silencio.

Tres oráculos medidos en este árbol antes de cerrarlos:

1. **Tiempo**: con un correo inexistente el login salía con 401 ANTES de
   calcular ningún bcrypt, así que respondía en microsegundos frente a las
   decenas de milisegundos de una cuenta real. Bastaba cronometrar.
2. **403**: `is_active` se comprobaba ANTES que la contraseña, de modo que un
   403 confirmaba que la cuenta existe sin saber su contraseña.
3. **Truncado**: bcrypt mira sólo los primeros 72 BYTES y descarta el resto
   **en silencio** (comprobado con bcrypt 4.3.0). Con `max_length=128` en el
   registro, el último tercio de una contraseña larga no protegía nada.
"""

import time
import uuid

import pytest

from core.security import hash_password, needs_rehash, verify_password


class TestContrasenaSinTruncado:
    def test_dos_contrasenas_con_los_mismos_72_primeros_bytes_NO_son_equivalentes(self):
        base = "a" * 72
        almacenada = hash_password(base + "-sufijo-uno")
        assert verify_password(base + "-sufijo-uno", almacenada) is True
        assert verify_password(base + "-sufijo-DOS", almacenada) is False
        # y la de 72 bytes pelada tampoco entra
        assert verify_password(base, almacenada) is False

    def test_el_esquema_antiguo_sigue_validando(self):
        """Cambiar el esquema no puede dejar fuera a quien ya tenía cuenta."""
        import bcrypt

        antigua = bcrypt.hashpw("secreta".encode(), bcrypt.gensalt()).decode()
        assert verify_password("secreta", antigua) is True
        assert verify_password("otra", antigua) is False
        assert needs_rehash(antigua) is True

    def test_el_esquema_nuevo_no_pide_rehash(self):
        assert needs_rehash(hash_password("secreta")) is False

    def test_una_contrasena_larguisima_no_revienta(self):
        larga = "ñ" * 500  # 1000 bytes en UTF-8
        almacenada = hash_password(larga)
        assert verify_password(larga, almacenada) is True
        assert verify_password("ñ" * 499, almacenada) is False


@pytest.mark.asyncio
class TestLoginSinOraculos:
    async def _registrar(self, client, email, password="Contrasena-1234"):
        r = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "gdpr_consent": True},
        )
        assert r.status_code == 201, r.text
        return password

    async def test_mismo_mensaje_exista_o_no_la_cuenta(self, client):
        email = f"{uuid.uuid4().hex}@ejemplo.com"
        await self._registrar(client, email)

        inexistente = await client.post(
            "/api/v1/auth/login",
            json={"email": f"{uuid.uuid4().hex}@ejemplo.com", "password": "loquesea"},
        )
        mal_password = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "incorrecta"}
        )
        assert inexistente.status_code == mal_password.status_code == 401
        assert inexistente.json()["detail"] == mal_password.json()["detail"]

    async def test_el_tiempo_no_delata_si_la_cuenta_existe(self, client):
        """Ambos caminos calculan un bcrypt; sin eso el inexistente volaba."""
        email = f"{uuid.uuid4().hex}@ejemplo.com"
        await self._registrar(client, email)

        async def cronometra(correo):
            muestras = []
            for _ in range(5):
                t0 = time.perf_counter()
                await client.post(
                    "/api/v1/auth/login",
                    json={"email": correo, "password": "incorrecta"},
                )
                muestras.append(time.perf_counter() - t0)
            return sorted(muestras)[len(muestras) // 2]

        real = await cronometra(email)
        falso = await cronometra(f"{uuid.uuid4().hex}@ejemplo.com")
        # El bcrypt domina el tiempo en ambos: la diferencia relativa se queda
        # muy por debajo del orden de magnitud que había antes.
        assert min(real, falso) > 0
        assert max(real, falso) / min(real, falso) < 3, (real, falso)

    async def test_una_cuenta_desactivada_solo_se_revela_con_la_contrasena_buena(
        self, client, db_session
    ):
        from sqlalchemy import select

        from models.user import User

        email = f"{uuid.uuid4().hex}@ejemplo.com"
        password = await self._registrar(client, email)
        usuario = (
            await db_session.execute(select(User).where(User.email == email))
        ).scalar_one()
        usuario.is_active = False
        await db_session.commit()

        con_mala = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "incorrecta"}
        )
        con_buena = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        # Sin la contraseña correcta no se entera de nada: 401, como cualquier
        # correo inexistente. Con ella, sí: es a quien le sirve saberlo.
        assert con_mala.status_code == 401
        assert con_buena.status_code == 403

    async def test_entrar_migra_el_hash_antiguo(self, client, db_session):
        """Quien entra con el esquema viejo sale con el nuevo, sin notarlo."""
        import bcrypt
        from sqlalchemy import select

        from models.user import User

        email = f"{uuid.uuid4().hex}@ejemplo.com"
        password = await self._registrar(client, email)
        usuario = (
            await db_session.execute(select(User).where(User.email == email))
        ).scalar_one()
        usuario.hashed_password = bcrypt.hashpw(
            password.encode(), bcrypt.gensalt()
        ).decode()
        await db_session.commit()
        assert needs_rehash(usuario.hashed_password) is True

        r = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert r.status_code == 200

        await db_session.refresh(usuario)
        assert needs_rehash(usuario.hashed_password) is False
        assert verify_password(password, usuario.hashed_password) is True
